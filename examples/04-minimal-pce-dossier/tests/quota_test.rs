use quota_dossier_example::allocate_quota;

#[test]
fn test_allocate_quota_clamps_to_cap() {
    assert_eq!(allocate_quota(100, 40).expect("valid"), 40);
    assert_eq!(allocate_quota(100, 250).expect("valid"), 100);
    assert!(allocate_quota(0, 10).is_err());
}
