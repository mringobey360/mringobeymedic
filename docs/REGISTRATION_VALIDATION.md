# Pharmacy Registration Validation

The public registration form requires the Pharmacy Council Facility Identification Number (FIN) for pharmacies. Business licence / permit number and expiry are optional.

## FIN format

The Pharmacy Council premises records and public examples use a seven-digit FIN, including leading zeroes (for example, `0101726`). The system therefore validates FIN as exactly seven digits.

## Licence / permit number

The licence / permit number is optional and is stored as entered. When supplied, the system keeps the exact transcription rather than inventing a format.

## Validity

The licence / permit expiry date is optional. When supplied, it cannot be in the past. This allows the system to know the licensing/permit period without deriving it from a number.

The system does not claim to verify a FIN against the Pharmacy Council live register unless an official verification API/integration is configured.
