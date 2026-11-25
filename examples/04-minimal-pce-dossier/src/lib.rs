use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum QuotaError {
    InvalidCap,
}

impl fmt::Display for QuotaError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidCap => f.write_str("quota cap or request units out of range"),
        }
    }
}

impl std::error::Error for QuotaError {}

/// @ensures result >= 0 and result <= cap_units
pub fn allocate_quota(cap_units: i64, requested_units: i64) -> Result<i64, QuotaError> {
    if cap_units < 1 || cap_units > 1_000_000 || requested_units < 0 {
        return Err(QuotaError::InvalidCap);
    }
    let granted = if requested_units > cap_units {
        cap_units
    } else {
        requested_units
    };
    debug_assert!(granted >= 0 && granted <= cap_units);
    Ok(granted)
}
