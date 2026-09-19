# GitHub HTTPS trust anchor

`github-root.pem` contains the public **USERTrust ECC Certification Authority**
root, extracted from the development host's trusted CA store on 18 September
2026. It contains no private key or account credential.

- DER SHA-256: `4ff460d54b9c86dabfbcfc5712e0400d2bed3fbc4d4fbdaa86e06adcd2a9ad7a`
- Expiry: 18 January 2038.
- Observed `api.github.com` chain: `*.github.com` ->
  `Sectigo Public Server Authentication CA DV E36` ->
  `Sectigo Public Server Authentication Root E46` ->
  `USERTrust ECC Certification Authority`.

The shipped cooperative HTTPS transport validated GitHub's current chain using
this bundle during a credential-free connectivity check (a synthetic invalid
bearer value received the expected HTTP 401). Local TLS tests also reject
untrusted roots, hostname mismatch and expired certificates before application
headers are transmitted.

This is a deliberately small **root trust bundle**, not a pinned GitHub leaf
certificate or a promise that GitHub will retain this issuer. Normal leaf
renewals under the same chain need no update. If GitHub changes its chain,
obtain the required root from a trusted CA distribution, independently verify
its subject/fingerprint and validity, update this file and its fingerprint test,
and redeploy it. A MicroPython `cafile` can contain multiple PEM roots if needed
for a verified transition. Do not trust a certificate solely because a failed
connection presented it, pin a short-lived leaf to work around missing roots,
or disable `CERT_REQUIRED`/hostname checks.

The actual board RTC must be set before certificate validity checks. NTP is
unauthenticated bootstrap; the firmware additionally compares verified HTTP Date
and persists a daily lower time bound. Physical access can modify both flash and
the stored credential, so this does not provide tamper-resistant storage.
