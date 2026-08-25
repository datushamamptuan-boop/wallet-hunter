# Wallet Hunter — Solana research app

Wallet Hunter discovers active Solana wallets and ranks them for **research/paper trading**. It is now a phone/PC-installable web app (PWA) served by GitHub Pages.

## What changed
- Keeps automatic wallet discovery, but labels it as candidate discovery rather than proof of trading.
- If `HELIUS_API_KEY` is configured, wallet history is decoded with Helius Enhanced Transactions, which classifies swaps instead of treating arbitrary token balance changes as buys/sells.
- Computes a conservative **realized SOL PnL** only for swaps whose Helius descriptions can be parsed into a SOL-funded buy and SOL-denominated exit.
- Computes closed-trade win rate and combines those with swap activity into the research score.
- Falls back to basic Solana RPC if no Helius key is present; fallback data is clearly labeled and does not pretend to be PnL.
- Includes a responsive PWA dashboard with manifest, service worker, and app icon.
- GitHub Actions runs automatically and publishes the dashboard.

Helius Enhanced Transactions provides structured transaction types such as `SWAP`, plus parsed transfer fields; this is substantially safer for swap detection than raw token balance deltas. Historical/indexing endpoints are also available for deeper backfills. See the official Helius docs for current limits and pricing.

## GitHub setup
1. Create a GitHub repository and upload the contents of this folder.
2. In **Settings → Secrets and variables → Actions**, add repository secret `HELIUS_API_KEY` with your Helius API key (recommended).
3. Optionally add repository variable `SOLANA_RPC_URL` with your paid RPC URL. If omitted, the public Solana RPC is used.
4. In **Settings → Pages**, set the source to **GitHub Actions**.
5. Open the workflow under **Actions → Wallet Hunter → Run workflow** once. Later runs happen automatically on the schedule.
6. Your project Pages URL will normally be `https://YOUR-USERNAME.github.io/REPOSITORY/`.

GitHub Pages supports custom workflows and HTTPS. The dashboard is static, so your API key stays in GitHub Actions and is never shipped to the browser.

## Phone + PC: make it an app
Open the Pages URL in Chrome/Edge/Safari. Use the browser's **Install app / Add to Home Screen** option. The PWA opens in its own window and can cache the dashboard shell for faster loading. No separate Android/iOS/Windows build is required.

## Important limitations
This is a research tool, not a guaranteed profitable-wallet detector. Realized PnL is conservative and incomplete: swaps paid/settled in stablecoins, transfers, bridges, aggregator descriptions that cannot be parsed, and positions without a clean round trip are excluded. Do not use the score as financial advice or as proof that a wallet will remain profitable.

No seed phrases, private keys, or automatic trade execution are used.
