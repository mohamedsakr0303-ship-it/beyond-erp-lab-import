# Signing the Windows apps

Windows (SmartScreen) and browsers warn about programs that are not signed with a code-signing certificate: "Windows
protected your PC", "not commonly downloaded". Copyright and publisher details in a program do not change that; only a
certificate issued to BEYONDERP by a certificate authority does, and the warnings then fade as the signed downloads build
reputation with Microsoft. (Even "EV" certificates no longer skip that reputation step.)

The desktop build (`.github/workflows/deploy.yml`, job *desktop*, run by hand) is ready: once a certificate is set up, it
signs and timestamps the ERP app (`BeyondERP.exe`), its installer and uninstaller, the BeyondERP Signer
(`BeyondERP-Signer.exe`), its installer and uninstaller, and checks every signature before publishing. Without one, it
builds them unsigned, as before. The signing is done by `packaging/sign.ps1`.

## Getting a certificate

* **Microsoft's Artifact Signing** (the cheapest, about $10 a month) currently accepts organisations in the USA, Canada,
  the EU and the UK only.
* An **OV code-signing certificate** from a certificate authority (SSL.com, Sectigo, DigiCert, GlobalSign, Certum, …)
  works for a business anywhere. Since 2023 the key must stay in a hardware token or the authority's cloud, so pick one
  with **cloud signing** for the build to use. The build already supports **SSL.com eSigner** (an OV certificate, about
  $129 a year, plus an eSigner signing plan; each desktop build makes 6 signatures).
* A certificate delivered as a `.pfx` file (for example a company certificate the key may be exported from) also works.

A self-signed certificate does not help: Windows does not trust it, so the warnings stay.

## Setting it up

GitHub → the repository → Settings → Secrets and variables → Actions → New repository secret:

| Certificate | Secrets |
|---|---|
| SSL.com eSigner | `ES_USERNAME`, `ES_PASSWORD`, `ES_CREDENTIAL_ID`, `ES_TOTP_SECRET` (from the eSigner page of the order: the account's sign-in, the certificate's credential ID, and the QR code's secret for one-time codes) |
| A `.pfx` file | `SIGN_PFX_BASE64` (the file as base64: `base64 -w0 company.pfx` or `[Convert]::ToBase64String([IO.File]::ReadAllBytes("company.pfx"))`) and `SIGN_PFX_PASSWORD` |

Then run the desktop build (Actions → deploy → Run workflow). Its log says *Signed:* for each file; right-click a
downloaded installer → Properties → Digital Signatures shows BEYONDERP.

The secrets are only readable by the build. Anyone who can change the workflow on `main` could sign with them, so keep
write access to the repository to the people who need it.
