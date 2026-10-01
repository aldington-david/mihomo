package updater

import (
	"runtime"
	"strings"
	"testing"
)

func TestForkReleaseSource(t *testing.T) {
	if baseReleaseURL != "https://github.com/aldington-david/mihomo/releases/latest/download/" || baseAlphaURL != baseReleaseURL || versionAlphaURL != versionReleaseURL {
		t.Fatal("a channel can leave the custom stable release source")
	}
	if runtime.GOARCH == "amd64" {
		previous := CoreFileSuffix
		defer func() { CoreFileSuffix = previous }()
		CoreFileSuffix = "-go120"
		if !strings.HasSuffix(DefaultCoreUpdater.CoreBaseName(), "-go120") {
			t.Fatal("legacy core updates must preserve their toolchain variant")
		}
	}
}
