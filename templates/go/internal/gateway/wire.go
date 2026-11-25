package gateway

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"github.com/futileandstupid/jezz-template-go/internal/domain"
)

var ErrPayloadBounds = errors.New("settlement JSON payload length outside permitted bounds")

func ParseAndQuoteJSON(rawJSON string) (domain.SettlementQuote, error) {
	if len(strings.TrimSpace(rawJSON)) < 2 || len(rawJSON) > 8192 {
		return domain.SettlementQuote{}, ErrPayloadBounds
	}
	dec := json.NewDecoder(strings.NewReader(rawJSON))
	dec.DisallowUnknownFields()

	var req domain.SettlementRequest
	if err := dec.Decode(&req); err != nil {
		return domain.SettlementQuote{}, fmt.Errorf("decode settlement request: %w", err)
	}
	var trailing struct{}
	if err := dec.Decode(&trailing); !errors.Is(err, io.EOF) {
		return domain.SettlementQuote{}, fmt.Errorf("unexpected trailing JSON token: %w", ErrPayloadBounds)
	}
	return domain.BuildSettlementQuote(req)
}
