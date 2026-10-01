package cli

import (
	"bytes"
	"context"
	"crypto/ecdh"
	"crypto/rand"
	"crypto/tls"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	anytls "github.com/anytls/sing-anytls"
	"github.com/anytls/sing-anytls/padding"
	"github.com/sagernet/sing/common/logger"
	M "github.com/sagernet/sing/common/metadata"
	N "github.com/sagernet/sing/common/network"
	"github.com/sagernet/sing/common/uot"
	"github.com/xtls/reality"
)

const password = "11111111-2222-4333-8444-555555555555"
const responseBody = "isolated-anytls-reality-ok"

// This handler only reaches the two loopback targets created by this test.
type relay struct{ tcp, udp string }

func (r relay) NewConnectionEx(ctx context.Context, conn net.Conn, _, dest M.Socksaddr, _ N.CloseHandlerFunc) {
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(8 * time.Second))
	if dest.Fqdn == uot.MagicAddress {
		request, err := uot.ReadRequest(conn)
		if err != nil {
			return
		}
		packet := uot.NewConn(conn, *request)
		b := make([]byte, 65535)
		n, addr, err := packet.ReadFrom(b)
		if err != nil || addr.String() != r.udp {
			return
		}
		upstream, err := net.DialTimeout("udp", r.udp, time.Second)
		if err != nil {
			return
		}
		defer upstream.Close()
		if err = N.ReportConnHandshakeSuccess(conn, upstream); err != nil {
			return
		}
		_ = upstream.SetDeadline(time.Now().Add(4 * time.Second))
		if _, err = upstream.Write(b[:n]); err != nil {
			return
		}
		n, err = upstream.Read(b)
		if err == nil {
			_, _ = packet.WriteTo(b[:n], addr)
		}
		return
	}
	if dest.String() != r.tcp {
		return
	}
	upstream, err := (&net.Dialer{Timeout: time.Second}).DialContext(ctx, "tcp", r.tcp)
	if err != nil {
		return
	}
	defer upstream.Close()
	if err = N.ReportConnHandshakeSuccess(conn, upstream); err != nil {
		return
	}
	go func() { _, _ = io.Copy(upstream, conn) }()
	_, _ = io.Copy(conn, upstream)
}

func TestCLIAnyTLSReality(t *testing.T) {
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	run := filepath.Join(root, "artifacts", "cli-"+time.Now().UTC().Format("20060102T150405Z"))
	if err = os.MkdirAll(run, 0700); err != nil {
		t.Fatal(err)
	}
	write := func(path string, data []byte) {
		t.Helper()
		if err := os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = io.WriteString(w, responseBody) }))
	defer target.Close()
	readyTarget := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = io.WriteString(w, "cli-ready") }))
	defer readyTarget.Close()
	udp, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer udp.Close()
	go func() {
		b := make([]byte, 65535)
		for {
			n, addr, err := udp.ReadFrom(b)
			if err != nil {
				return
			}
			_, _ = udp.WriteTo(b[:n], addr)
		}
	}()
	decoy := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = io.WriteString(w, "local-decoy") }))
	decoy.EnableHTTP2 = true
	decoy.TLS = &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13}
	decoy.StartTLS()
	defer decoy.Close()
	// The decoy's test certificate cannot pass public-root verification. Seed only
	// the normal empty post-handshake profile. Cryptographic REALITY and AnyTLS
	// authentication still run on real sockets. This does not test a public decoy.
	for _, alpn := range []string{"0", "1", "2"} {
		key := decoy.Listener.Addr().String() + " example.com " + alpn
		reality.GlobalPostHandshakeRecordsLens.Store(key, []int{})
		defer reality.GlobalPostHandshakeRecordsLens.Delete(key)
	}
	key, err := ecdh.X25519().GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	wrongKey, err := ecdh.X25519().GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	realityConfig := &reality.Config{
		SessionTicketsDisabled: true, // Required by the REALITY server, as in Xray and sing-box.
		DialContext:            (&net.Dialer{Timeout: 3 * time.Second}).DialContext,
		Type:                   "tcp", Dest: decoy.Listener.Addr().String(), ServerNames: map[string]bool{"example.com": true},
		PrivateKey: key.Bytes(), ShortIds: map[[8]byte]bool{{1, 35, 69, 103, 137, 171, 205, 239}: true},
	}
	service, err := anytls.NewService(anytls.ServiceConfig{
		Users: []anytls.User{{Name: "loopback-test", Password: password}}, PaddingScheme: padding.DefaultPaddingScheme,
		Handler: relay{tcp: target.Listener.Addr().String(), udp: udp.LocalAddr().String()}, Logger: logger.NOP(),
	})
	if err != nil {
		t.Fatal(err)
	}
	var serverMu sync.Mutex
	var serverEvents []string
	startServer := func(isReality bool) int {
		listener, err := net.Listen("tcp4", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = listener.Close() })
		go func() {
			for {
				conn, err := listener.Accept()
				if err != nil {
					return
				}
				go func() {
					defer conn.Close()
					_ = conn.SetDeadline(time.Now().Add(8 * time.Second))
					var secured net.Conn
					var err error
					if isReality {
						secured, err = reality.Server(context.Background(), conn, realityConfig)
					} else {
						secured = tls.Server(conn, &tls.Config{Certificates: decoy.TLS.Certificates, MinVersion: tls.VersionTLS12})
					}
					if err == nil {
						err = service.NewConnection(context.Background(), secured, M.SocksaddrFromNet(conn.RemoteAddr()), nil)
					}
					if err != nil {
						serverMu.Lock()
						serverEvents = append(serverEvents, fmt.Sprintf("reality=%t: %v", isReality, err))
						serverMu.Unlock()
					}
				}()
			}
		}()
		return listener.Addr().(*net.TCPAddr).Port
	}
	realityPort, tlsPort := startServer(true), startServer(false)
	results := map[string]any{"scope": "loopback only; local TLS 1.3 decoy with seeded empty profile; no production clients or WAN", "anytls_service": "github.com/anytls/sing-anytls v0.0.11", "reality_service": "github.com/xtls/reality v0.0.0-20251014195629-e4eec4520535"}
	defer func() {
		serverMu.Lock()
		results["server_events"] = append([]string(nil), serverEvents...)
		serverMu.Unlock()
		raw, _ := json.MarshalIndent(results, "", "  ")
		write(filepath.Join(run, "results.json"), raw)
		t.Logf("Evidence: %s", run)
	}()
	for _, label := range []string{"official", "anytls-reality"} {
		t.Run(label, func(t *testing.T) {
			exe := filepath.Join(root, "artifacts", "mihomo-"+label+".exe")
			if _, err := os.Stat(exe); err != nil {
				t.Fatal(err)
			}
			home := filepath.Join(run, label)
			if err := os.MkdirAll(home, 0700); err != nil {
				t.Fatal(err)
			}
			command := func(ctx context.Context, args ...string) *exec.Cmd {
				cmd := exec.CommandContext(ctx, exe, args...)
				cmd.Dir = home
				cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
				return cmd
			}
			version, err := command(context.Background(), "-v", "-d", home).CombinedOutput()
			if err != nil {
				t.Fatal(err)
			}
			coreResults := map[string]any{"version": strings.TrimSpace(string(version))}
			results[label] = coreResults
			base := map[string]any{"name": "TEST", "type": "anytls", "server": "127.0.0.1", "port": realityPort, "password": password, "udp": true, "sni": "example.com", "client-fingerprint": "chrome", "reality-opts": map[string]any{"public-key": base64.RawURLEncoding.EncodeToString(key.PublicKey().Bytes()), "short-id": "0123456789abcdef"}}
			clone := func() map[string]any {
				raw, _ := json.Marshal(base)
				var node map[string]any
				_ = json.Unmarshal(raw, &node)
				return node
			}
			profile := func(node map[string]any, port int) []byte {
				raw, _ := json.MarshalIndent(map[string]any{"mixed-port": port, "bind-address": "127.0.0.1", "allow-lan": false, "mode": "rule", "log-level": "debug", "external-controller": "", "find-process-mode": "off", "tun": map[string]bool{"enable": false}, "dns": map[string]bool{"enable": false}, "geo-auto-update": false, "proxies": []any{node}, "rules": []string{fmt.Sprintf("DST-PORT,%d,DIRECT", readyTarget.Listener.Addr().(*net.TCPAddr).Port), "MATCH,TEST"}}, "", "  ")
				return raw
			}
			check := func(name string, raw []byte) bool {
				path := filepath.Join(home, name+".yaml")
				write(path, raw)
				ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
				defer cancel()
				out, err := command(ctx, "-t", "-d", home, "-f", path).CombinedOutput()
				write(filepath.Join(home, name+"-check.log"), out)
				return err == nil
			}
			runProxy := func(name string, node map[string]any, want bool, testUDP bool) {
				t.Run(name, func(t *testing.T) {
					probe, err := net.Listen("tcp4", "127.0.0.1:0")
					if err != nil {
						t.Fatal(err)
					}
					port := probe.Addr().(*net.TCPAddr).Port
					addr := probe.Addr().String()
					_ = probe.Close()
					raw := profile(node, port)
					valid := check(name, raw)
					if !valid {
						t.Fatalf("expected valid syntax: %s", name)
					}
					logFile, err := os.Create(filepath.Join(home, name+"-runtime.log"))
					if err != nil {
						t.Fatal(err)
					}
					defer logFile.Close()
					ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
					defer cancel()
					cmd := command(ctx, "-d", home, "-f", filepath.Join(home, name+".yaml"))
					cmd.Stdout = logFile
					cmd.Stderr = logFile
					if err = cmd.Start(); err != nil {
						t.Fatal(err)
					}
					defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait() }()
					proxyURL, _ := url.Parse("http://" + addr)
					transport := &http.Transport{Proxy: http.ProxyURL(proxyURL), DisableKeepAlives: true}
					defer transport.CloseIdleConnections()
					// A bound port precedes tunnel.OnRunning. Probe a dedicated DIRECT
					// target so readiness never retries/masks the actual AnyTLS assertion.
					ready := false
					for until := time.Now().Add(5 * time.Second); time.Now().Before(until); {
						response, err := (&http.Client{Transport: transport, Timeout: 500 * time.Millisecond}).Get(readyTarget.URL)
						if err == nil {
							body, readErr := io.ReadAll(response.Body)
							_ = response.Body.Close()
							ready = readErr == nil && response.StatusCode == 200 && string(body) == "cli-ready"
						}
						if ready {
							break
						}
						time.Sleep(50 * time.Millisecond)
					}
					if !ready {
						t.Fatal("CLI did not complete its DIRECT readiness request")
					}
					response, reqErr := (&http.Client{Transport: transport, Timeout: 4 * time.Second}).Get(target.URL)
					ok := false
					detail := fmt.Sprint(reqErr)
					if reqErr == nil {
						body, readErr := io.ReadAll(response.Body)
						_ = response.Body.Close()
						ok = readErr == nil && response.StatusCode == 200 && string(body) == responseBody
						detail = fmt.Sprintf("HTTP %d body=%q read_error=%v", response.StatusCode, body, readErr)
					}
					item := map[string]any{"config_valid": valid, "ready_http_ok": ready, "http_ok": ok, "http_detail": detail, "expected_http_ok": want}
					coreResults[name] = item
					if ok != want {
						t.Errorf("HTTP success=%t, want=%t: %s", ok, want, detail)
					}
					if testUDP {
						err := udpViaSOCKS(addr, udp.LocalAddr().(*net.UDPAddr))
						item["udp_ok"] = err == nil
						item["udp_detail"] = fmt.Sprint(err)
						if err != nil {
							t.Errorf("UDP: %v", err)
						}
					}
					t.Logf("HTTP success=%t (expected %t), %s", ok, want, detail)
				})
			}
			plain := clone()
			delete(plain, "reality-opts")
			plain["port"] = tlsPort
			plain["skip-cert-verify"] = true
			runProxy("ordinary-tls", plain, true, true)
			runProxy("reality", clone(), label != "official", label != "official")
			if label == "official" {
				insecure := clone()
				insecure["skip-cert-verify"] = true
				runProxy("reality-skip-cert", insecure, false, false)
			}
			if label != "official" {
				wrongPassword := clone()
				wrongPassword["password"] = "wrong-password"
				runProxy("wrong-password", wrongPassword, false, false)
				wrongPublic := clone()
				wrongPublic["reality-opts"].(map[string]any)["public-key"] = base64.RawURLEncoding.EncodeToString(wrongKey.PublicKey().Bytes())
				runProxy("wrong-public-key", wrongPublic, false, false)
				wrongPublic["skip-cert-verify"] = true
				runProxy("wrong-public-key-skip-cert", wrongPublic, false, false)
				wrongID := clone()
				wrongID["reality-opts"].(map[string]any)["short-id"] = "0123456789abcdee"
				runProxy("wrong-short-id", wrongID, false, false)
				wrongSNI := clone()
				wrongSNI["sni"] = "wrong.example"
				runProxy("wrong-sni", wrongSNI, false, false)
			}
			invalids := map[string]func(map[string]any){
				"invalid-public-key":  func(n map[string]any) { n["reality-opts"].(map[string]any)["public-key"] = "not-a-key" },
				"invalid-short-id":    func(n map[string]any) { n["reality-opts"].(map[string]any)["short-id"] = "not-hex" },
				"missing-fingerprint": func(n map[string]any) { delete(n, "client-fingerprint") },
				"invalid-fingerprint": func(n map[string]any) { n["client-fingerprint"] = "invalid-fingerprint" },
			}
			for name, mutate := range invalids {
				node := clone()
				mutate(node)
				accepted := check(name, profile(node, 0))
				coreResults[name] = map[string]bool{"config_accepted": accepted}
				if accepted != (label == "official") {
					t.Errorf("%s accepted=%t", name, accepted)
				}
			}
		})
	}
}

// Minimal SOCKS5 UDP associate exercises actual datagrams through the CLI/UoT path.
func udpViaSOCKS(proxy string, target *net.UDPAddr) error {
	control, err := net.DialTimeout("tcp", proxy, time.Second)
	if err != nil {
		return err
	}
	defer control.Close()
	_ = control.SetDeadline(time.Now().Add(4 * time.Second))
	if _, err = control.Write([]byte{5, 1, 0}); err != nil {
		return err
	}
	reply := make([]byte, 2)
	if _, err = io.ReadFull(control, reply); err != nil {
		return err
	}
	if !bytes.Equal(reply, []byte{5, 0}) {
		return fmt.Errorf("SOCKS auth reply %v", reply)
	}
	if _, err = control.Write([]byte{5, 3, 0, 1, 127, 0, 0, 1, 0, 0}); err != nil {
		return err
	}
	reply = make([]byte, 4)
	if _, err = io.ReadFull(control, reply); err != nil {
		return err
	}
	if reply[0] != 5 || reply[1] != 0 {
		return fmt.Errorf("SOCKS associate reply %v", reply)
	}
	var ip net.IP
	switch reply[3] {
	case 1:
		ip = make(net.IP, 4)
	case 4:
		ip = make(net.IP, 16)
	default:
		return fmt.Errorf("unexpected relay address type %d", reply[3])
	}
	if _, err = io.ReadFull(control, ip); err != nil {
		return err
	}
	p := make([]byte, 2)
	if _, err = io.ReadFull(control, p); err != nil {
		return err
	}
	if ip.IsUnspecified() {
		ip = net.IPv4(127, 0, 0, 1)
	}
	if !ip.IsLoopback() {
		return fmt.Errorf("non-loopback relay %s", ip)
	}
	conn, err := net.DialUDP("udp4", nil, &net.UDPAddr{IP: ip, Port: int(binary.BigEndian.Uint16(p))})
	if err != nil {
		return err
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(4 * time.Second))
	packet := []byte{0, 0, 0, 1, 127, 0, 0, 1, byte(target.Port >> 8), byte(target.Port)}
	packet = append(packet, []byte(responseBody)...)
	if _, err = conn.Write(packet); err != nil {
		return err
	}
	b := make([]byte, 65535)
	n, err := conn.Read(b)
	if err != nil {
		return err
	}
	if !bytes.Equal(b[:n], packet) {
		return fmt.Errorf("UDP reply differs: %x", b[:n])
	}
	return nil
}
