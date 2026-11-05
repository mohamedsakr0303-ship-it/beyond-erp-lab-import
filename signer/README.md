# BeyondERP Signer

A small program that runs on the computer the e-invoicing USB token is plugged into. The ERP (in the browser, the
desktop app or the hosted site) asks it to sign documents and to talk to the Egyptian Tax Authority (ETA), so the
token, its PIN and the ETA client secret never leave that computer.

## Install

- **Windows:** download `BeyondERP-Signer.exe` (ERP → E-invoicing → Settings, or the Downloads page) and run it.
  Install the token's own driver first (for example the ePass2003 driver from the token's supplier).
- **Mac / Linux:** `pip install -r signer/requirements.txt`, then `python3 signer/beyond_signer.py`.

Keep its window open while signing. It answers only on this computer, at `http://127.0.0.1:47321`.

## Set up (once)

1. The Signer opens its settings page in the browser (`http://127.0.0.1:47321/`). Choose:
   - **USB token**: the token's PKCS#11 driver file (found by itself for common tokens), or
   - **Certificate file**: a `.pfx` / `.p12` file.
2. Enter the ETA **client ID and client secret** of the company's ERP system (from the ETA portal), and the environment
   (pre-production for testing, production for real invoices). Secrets are stored encrypted with Windows DPAPI; on other
   systems the settings file is readable by the current user only.
3. Copy the **pairing key** shown there into ERP → E-invoicing → Settings → BeyondERP Signer on this computer, then press
   **Test**. The key is stored in that browser only.

## Use

In the ERP: E-invoicing → Issued → tick invoices → **Sign and send ticked**. The token PIN is asked the first time and
kept in the Signer's memory for 30 minutes (changeable in its settings). From an invoice: check the signature, refresh its status, download the signed document or the
ETA PDF, cancel it. E-invoicing → Received fetches what suppliers issued to the company, to record or reject.

## Notes

- Safari on Mac blocks web pages from reaching programs on the same computer: use Chrome, Edge or the desktop app.
- Phones and tablets can view e-invoices but cannot sign (the token is not plugged into them).
- The signature is CAdES-BES (detached CMS, SHA-256, signing-certificate-v2), the format ETA's own signing tool produces.
  Test on ETA pre-production with the real token and credentials before signing real invoices.
- Endpoints: `/v1/hello` (no key), `/v1/status`, `/v1/unlock`, `/v1/lock`, `/v1/sign`, `/v1/verify`, `/v1/eta/test`,
  `/v1/eta/submit`, `/v1/eta/document/{uuid}/{details|raw|pdf|state}`, `/v1/eta/recent`. All but `/v1/hello` need the
  `X-Signer-Key` header.
