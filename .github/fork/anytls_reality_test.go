package outbound

import (
	"encoding/base64"
	"strings"
	"testing"

	"github.com/metacubex/mihomo/common/structure"
)

func TestAnyTLSRealityOptions(t *testing.T) {
	key := base64.RawURLEncoding.EncodeToString(make([]byte, 32))
	for _, tc := range []struct {
		name      string
		extra     map[string]any
		wantError string
	}{
		{"ordinary TLS", nil, ""},
		{"REALITY", map[string]any{"reality-opts": map[string]any{"public-key": key, "short-id": "0123456789abcdef"}}, ""},
		{"empty short ID", map[string]any{"reality-opts": map[string]any{"public-key": key}}, ""},
		{"empty block", map[string]any{"reality-opts": map[string]any{}}, "unset fields: public-key"},
		{"missing key", map[string]any{"reality-opts": map[string]any{"short-id": "01"}}, "unset fields: public-key"},
		{"empty key", map[string]any{"reality-opts": map[string]any{"public-key": ""}}, "requires a public-key"},
		{"invalid key", map[string]any{"reality-opts": map[string]any{"public-key": "invalid"}}, "invalid REALITY public key"},
		{"odd short ID", map[string]any{"reality-opts": map[string]any{"public-key": key, "short-id": "1"}}, "invalid REALITY short ID"},
		{"long short ID", map[string]any{"reality-opts": map[string]any{"public-key": key, "short-id": "0123456789abcdef00"}}, "invalid REALITY short id"},
		{"missing fingerprint", map[string]any{"reality-opts": map[string]any{"public-key": key}, "client-fingerprint": ""}, "requires a valid client-fingerprint"},
		{"invalid fingerprint", map[string]any{"reality-opts": map[string]any{"public-key": key}, "client-fingerprint": "invalid"}, "requires a valid client-fingerprint"},
		{"ECH conflict", map[string]any{"reality-opts": map[string]any{"public-key": key}, "ech-opts": map[string]any{"enable": true}}, "mutually exclusive"},
		{"ShadowTLS conflict", map[string]any{"reality-opts": map[string]any{"public-key": key}, "shadow-tls-opts": map[string]any{"version": 3, "password": "test"}}, "mutually exclusive"},
		{"Restls conflict", map[string]any{"reality-opts": map[string]any{"public-key": key}, "restls-opts": map[string]any{"version-hint": "tls13", "password": "test"}}, "mutually exclusive"},
		{"JLS conflict", map[string]any{"reality-opts": map[string]any{"public-key": key}, "jls-opts": map[string]any{"username": "test", "password": "test"}}, "mutually exclusive"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			mapping := map[string]any{"name": "test", "server": "127.0.0.1", "port": 443, "password": "test", "sni": "example.com", "client-fingerprint": "chrome"}
			for k, v := range tc.extra {
				mapping[k] = v
			}
			var option AnyTLSOption
			decoder := structure.NewDecoder(structure.Option{TagName: "proxy", WeaklyTypedInput: true})
			err := decoder.Decode(mapping, &option)
			if err == nil {
				var client *AnyTLS
				client, err = NewAnyTLS(option)
				if client != nil {
					defer client.Close()
				}
			}
			if tc.wantError == "" {
				if err != nil {
					t.Fatal(err)
				}
			} else if err == nil || !strings.Contains(err.Error(), tc.wantError) {
				t.Fatalf("want error containing %q, got %v", tc.wantError, err)
			}
		})
	}
}
