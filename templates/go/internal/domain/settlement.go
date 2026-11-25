package domain

import (
	"errors"
	"strings"
)

var (
	ErrInvalidAccountID = errors.New("account_id length must be within 3..64")
	ErrOutOfRangeAmount = errors.New("settlement cents or basis points out of range")
)

type SettlementRequest struct {
	AccountID  string `json:"account_id"`
	GrossCents int64  `json:"gross_cents"`
	FeeBps     int64  `json:"fee_bps"`
}

func (r SettlementRequest) Validate() error {
	trimmed := strings.TrimSpace(r.AccountID)
	if len(trimmed) < 3 || len(trimmed) > 64 {
		return ErrInvalidAccountID
	}
	if r.GrossCents < 1 || r.GrossCents > 1_000_000_000 || r.FeeBps < 0 || r.FeeBps > 10_000 {
		return ErrOutOfRangeAmount
	}
	return nil
}

type SettlementQuote struct {
	AccountID  string `json:"account_id"`
	GrossCents int64  `json:"gross_cents"`
	FeeCents   int64  `json:"fee_cents"`
	NetCents   int64  `json:"net_cents"`
}

func (q SettlementQuote) Validate() error {
	trimmed := strings.TrimSpace(q.AccountID)
	if len(trimmed) < 3 || len(trimmed) > 64 {
		return ErrInvalidAccountID
	}
	if q.GrossCents < 1 || q.FeeCents < 0 || q.NetCents < 0 || q.FeeCents+q.NetCents != q.GrossCents {
		return ErrOutOfRangeAmount
	}
	return nil
}

// @ensures result >= 0 and result <= grossCents
func NetAfterFee(grossCents int64, feeBps int64) (int64, error) {
	if grossCents < 1 || grossCents > 1_000_000_000 || feeBps < 0 || feeBps > 10_000 {
		return 0, ErrOutOfRangeAmount
	}
	feeCents := (grossCents * feeBps) / 10_000
	return grossCents - feeCents, nil
}

func BuildSettlementQuote(req SettlementRequest) (SettlementQuote, error) {
	if err := req.Validate(); err != nil {
		return SettlementQuote{}, err
	}
	netCents, err := NetAfterFee(req.GrossCents, req.FeeBps)
	if err != nil {
		return SettlementQuote{}, err
	}
	quote := SettlementQuote{
		AccountID:  req.AccountID,
		GrossCents: req.GrossCents,
		FeeCents:   req.GrossCents - netCents,
		NetCents:   netCents,
	}
	if err := quote.Validate(); err != nil {
		return SettlementQuote{}, err
	}
	return quote, nil
}
