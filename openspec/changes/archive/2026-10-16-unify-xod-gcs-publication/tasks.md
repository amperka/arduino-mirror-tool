## Implementation

- [x] Verify rclone ADC/WIF and bucket-policy-only support.
- [x] Switch XOD to the shared GCS bucket and WIF while preserving build/order/scope.
- [x] Update the baseline specification and deployment documentation.
- [x] Add focused workflow and offline runtime checks; run regression validation.

## Deployment boundary

No push, production workflow run, secret deletion, IAM change, or live object
migration is part of this local change.
