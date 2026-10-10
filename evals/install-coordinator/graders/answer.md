---
type: llm
---
PASS when the answer does all of these:
- says to download the macOS coordinator installer (a .pkg) from the Oarbank release page on GitHub;
- says to check the download against the release's SHA256SUMS (a checksum);
- describes the first-run setup in the browser (opening the web app / setup wizard, choosing an address the other computers can reach, an administrator password, an authenticator app);
- mentions keeping the backup owner key somewhere safe and offline.
FAIL when it invents a Homebrew formula, a curl-pipe-to-shell installer or a command-line installer for the coordinator, or when it claims to have installed anything itself.
