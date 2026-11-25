package sunsetvariance_test

import (
	"testing"

	"example.com/sunsetvariance"
)

func TestTranslateLegacyOrder(t *testing.T) {
	total, err := sunsetvariance.TranslateLegacyOrder(10, 20, 30, 40, 50, 60, 70)
	if err != nil || total != 280 {
		t.Fatalf("expected 280, got %d (%v)", total, err)
	}
}
