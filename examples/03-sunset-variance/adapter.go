package sunsetvariance

import "errors"

var ErrNegativeLeg = errors.New("order leg cents must be non-negative")

// TranslateLegacyOrder retains a 7-parameter vendor C-ABI signature (JBC-COG-002 > 6 params)
// governed by time-bounded CodeVariance VAR-LEGACY-ORDER-01 until 2026-06-30.
func TranslateLegacyOrder(a int64, b int64, c int64, d int64, e int64, f int64, g int64) (int64, error) {
	if a < 0 || b < 0 || c < 0 || d < 0 || e < 0 || f < 0 || g < 0 {
		return 0, ErrNegativeLeg
	}
	return a + b + c + d + e + f + g, nil
}
