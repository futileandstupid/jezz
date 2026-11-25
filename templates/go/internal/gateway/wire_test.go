package gateway_test

import (
	"testing"
	"testing/quick"

	"github.com/futileandstupid/jezz-template-go/internal/domain"
	"github.com/futileandstupid/jezz-template-go/internal/gateway"
)

func TestPropertySettlementConservation(t *testing.T) {
	prop := func(rawGross uint32, rawFee uint16) bool {
		gross := int64(rawGross%50_000_000) + 1
		feeBps := int64(rawFee % 10_001)
		req := domain.SettlementRequest{
			AccountID:  "acct_go_01",
			GrossCents: gross,
			FeeBps:     feeBps,
		}
		quote, err := domain.BuildSettlementQuote(req)
		if err != nil {
			return false
		}
		net, err := domain.NetAfterFee(gross, feeBps)
		if err != nil {
			return false
		}
		return quote.NetCents >= 0 &&
			quote.NetCents <= gross &&
			quote.FeeCents+quote.NetCents == gross &&
			net == quote.NetCents
	}
	if err := quick.Check(prop, nil); err != nil {
		t.Fatalf("conservation property violated: %v", err)
	}
}

func TestGatewayAcceptsCanonicalAndRejectsUnknownFields(t *testing.T) {
	valid := `{"account_id":"acct_primary","gross_cents":10000,"fee_bps":250}`
	quote, err := gateway.ParseAndQuoteJSON(valid)
	if err != nil {
		t.Fatalf("unexpected error on canonical payload: %v", err)
	}
	if quote.FeeCents != 250 || quote.NetCents != 9750 {
		t.Fatalf("unexpected quote values: %+v", quote)
	}

	spoofed := `{"account_id":"acct_primary","gross_cents":10000,"fee_bps":250,"role":"admin"}`
	if _, err := gateway.ParseAndQuoteJSON(spoofed); err == nil {
		t.Fatal("expected DisallowUnknownFields to reject unknown key 'role'")
	}
}
