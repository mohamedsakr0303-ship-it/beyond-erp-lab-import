# BeyondERP Signer

A small program that runs on the computer the e-invoicing USB token is plugged into. The ERP (in the browser, the
desktop app or the hosted site) asks it to sign documents and to talk to the Egyptian Tax Authority (ETA), so the
token, its PIN and the ETA client secret never leave that computer.

## Install

- **Windows:** download `BeyondERP-Signer-Setup.exe` (ERP → E-invoicing → Settings, or the Downloads page) and run it.
  It installs for the signed-in Windows user (no administrator rights): a Start-menu entry, start with Windows (on
  by default) and an uninstaller in Settings → Apps. Install the token's own driver first (for example the ePass2003
  driver from the token's supplier).
- **Mac / Linux:** `pip install -r signer/requirements.txt`, then `python3 signer/beyond_signer.py`.

On Windows the Signer has no console window. It runs in the background with its icon by the clock (right-click it:
Open, Start with Windows, Quit), and its settings open in a window of their own. Starting it again while it runs only
opens that window. It answers on this computer only (`localhost`, port 47321), and only to this computer's own names, so
a website that points its own name at this computer is refused.

## Set up (once)

1. The Signer opens its settings window. Choose:
   - **USB token**: the token's PKCS#11 driver file (found by itself for common tokens), or
   - **Certificate file**: a `.pfx` / `.p12` file.
2. Enter the ETA **client ID and client secret** of the company's ERP system (from the ETA portal), and the environment
   (pre-production for testing, production for real invoices). Secrets are stored encrypted with Windows DPAPI; on other
   systems the settings file is readable by the current user only.
3. Copy the **pairing key** shown there into ERP → E-invoicing → Settings → BeyondERP Signer on this computer, then press
   **Test**. The key is stored in that browser only.

The settings change only from the Signer's own window: each change carries that window's token, and a form posted from
any other website is refused.

## Use

In the ERP: E-invoicing → Issued → tick invoices → **Sign and send ticked**. The token PIN is asked the first time and
kept in the Signer's memory for 30 minutes (changeable in its settings). From an invoice: check the signature, refresh its status, download the signed document or the
ETA PDF, cancel it. E-invoicing → Received fetches what suppliers issued to the company, to record or reject.

## Windows warnings ("Windows protected your PC", "not commonly downloaded")

Windows SmartScreen and browsers warn about any program that is not signed with a code-signing certificate, whatever
its name or copyright says. The Signer carries BEYONDERP as its publisher, its copyright and version in its properties,
and comes in a normal installer, but the warning only goes away once the installer and program are signed with a
code-signing certificate issued to BEYONDERP, and that signature has built reputation with Microsoft. Once a certificate
is set up, signing becomes one step in the desktop build (`.github/workflows/deploy.yml`, before the installer is
published).

## Notes

- Safari on Mac blocks web pages from reaching programs on the same computer: use Chrome, Edge or the desktop app.
- Phones and tablets can view e-invoices but cannot sign (the token is not plugged into them).
- The signature is CAdES-BES (detached CMS, SHA-256, signing-certificate-v2), the format ETA's own signing tool produces.
  Test on ETA pre-production with the real token and credentials before signing real invoices.
- Endpoints: `/v1/hello` (no key), `/v1/status`, `/v1/unlock`, `/v1/lock`, `/v1/sign`, `/v1/verify`, `/v1/eta/test`,
  `/v1/eta/submit`, `/v1/eta/document/{uuid}/{details|raw|pdf|state}`, `/v1/eta/recent`. All but `/v1/hello` need the
  `X-Signer-Key` header.
