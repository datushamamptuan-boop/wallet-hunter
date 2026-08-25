import json
import os
import re
import time
from pathlib import Path
from collections import defaultdict

import requests
from solana_rpc import SolanaRPC


ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config/programs.json"
IN = ROOT / "data/discovery.json"
OUT = ROOT / "data/rankings.json"

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "").strip()
HELIUS_BASE = "https://api.helius.xyz/v0/addresses/{address}/transactions"


STABLES = {"SOL", "USDC", "USDT"}


def get_asset_mint(item):
    if not isinstance(item, dict):
        return None

    return (
        item.get("mint")
        or item.get("tokenMint")
        or item.get("token_mint")
    )


def get_amount(item):
    if not isinstance(item, dict):
        return 0.0

    for key in ("tokenAmount", "amount", "rawTokenAmount"):
        value = item.get(key)

        if isinstance(value, dict):
            value = (
                value.get("tokenAmount")
                or value.get("amount")
            )

        try:
            return float(value)
        except (TypeError, ValueError):
            pass

    return 0.0


def get_symbol(item):
    if not isinstance(item, dict):
        return None

    return (
        item.get("symbol")
        or item.get("tokenSymbol")
        or item.get("asset")
    )


def parse_description(description):
    """
    Backup parser for Helius descriptions.

    Handles common forms such as:
    'Swapped 1.2 SOL for 500 TOKEN'
    """

    if not description:
        return None

    patterns = [
        r"Swapped\s+(.+?)\s+for\s+(.+?)(?:\s+via\s+|$)",
        r"Swap\s+(.+?)\s+for\s+(.+?)(?:\s+via\s+|$)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            description,
            re.IGNORECASE,
        )

        if not match:
            continue

        give = re.search(
            r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*([A-Z0-9]{2,12})",
            match.group(1),
            re.IGNORECASE,
        )

        receive = re.search(
            r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*([A-Z0-9]{2,12})",
            match.group(2),
            re.IGNORECASE,
        )

        if give and receive:
            return {
                "spent_amount": float(
                    give.group(1).replace(",", "")
                ),
                "spent_asset": give.group(2).upper(),
                "received_amount": float(
                    receive.group(1).replace(",", "")
                ),
                "received_asset": receive.group(2).upper(),
            }

    return None


def helius_history(address, limit):
    if not HELIUS_KEY:
        return []

    url = HELIUS_BASE.format(address=address)

    output = []
    before = None

    while len(output) < limit:
        params = {
            "api-key": HELIUS_KEY,
            "limit": min(100, limit - len(output)),
        }

        if before:
            params["before-signature"] = before

        response = requests.get(
            url,
            params=params,
            timeout=45,
        )

        response.raise_for_status()

        page = response.json() or []

        if not page:
            break

        output.extend(page)

        before = page[-1].get("signature")

        if len(page) < 100:
            break

    return output[:limit]


def decode_swap(tx):
    """
    Try structured Helius swap fields first.

    If those aren't available, fall back to the
    transaction description.
    """

    token_inputs = tx.get("tokenInputs") or []
    token_outputs = tx.get("tokenOutputs") or []

    if token_inputs and token_outputs:
        spent = token_inputs[0]
        received = token_outputs[0]

        spent_asset = (
            get_symbol(spent)
            or get_asset_mint(spent)
        )

        received_asset = (
            get_symbol(received)
            or get_asset_mint(received)
        )

        spent_amount = get_amount(spent)
        received_amount = get_amount(received)

        if (
            spent_asset
            and received_asset
            and spent_amount > 0
            and received_amount > 0
        ):
            return {
                "spent_amount": spent_amount,
                "spent_asset": str(spent_asset).upper(),
                "received_amount": received_amount,
                "received_asset": str(received_asset).upper(),
                "method": "structured",
            }

    parsed = parse_description(
        tx.get("description", "")
    )

    if parsed:
        parsed["method"] = "description"
        return parsed

    return None


def analyze_helius(address, limit):
    txs = helius_history(address, limit)

    swaps = [
        tx
        for tx in txs
        if str(tx.get("type", "")).upper() == "SWAP"
    ]

    buys = 0
    sells = 0

    pnl = 0.0

    open_qty = defaultdict(float)
    open_cost = defaultdict(float)

    wins = 0
    losses = 0

    trades = []

    for tx in reversed(swaps):
        swap = decode_swap(tx)

        if not swap:
            continue

        spent_amount = swap["spent_amount"]
        spent_asset = swap["spent_asset"]

        received_amount = swap["received_amount"]
        received_asset = swap["received_asset"]

        # BUY:
        # SOL / USDC / USDT -> token
        if (
            spent_asset in STABLES
            and received_asset not in STABLES
        ):
            buys += 1

            token = received_asset

            open_qty[token] += received_amount
            open_cost[token] += spent_amount

            trades.append({
                "signature": tx.get("signature"),
                "side": "BUY",
                "asset": token,
                "qty": received_amount,
                "cost": spent_amount,
                "cost_asset": spent_asset,
                "timestamp": tx.get("timestamp"),
            })

        # SELL:
        # token -> SOL / USDC / USDT
        elif (
            spent_asset not in STABLES
            and received_asset in STABLES
        ):
            sells += 1

            token = spent_asset
            qty_sold = spent_amount

            if open_qty[token] > 0:
                matched_qty = min(
                    qty_sold,
                    open_qty[token],
                )

                avg_cost = (
                    open_cost[token]
                    / open_qty[token]
                )

                cost_basis = avg_cost * matched_qty

                # We calculate realized PnL when
                # the proceeds are in SOL.
                if received_asset == "SOL":
                    profit = (
                        received_amount
                        - cost_basis
                    )

                    pnl += profit

                    if profit > 0:
                        wins += 1
                    elif profit < 0:
                        losses += 1

                open_qty[token] = max(
                    0,
                    open_qty[token] - matched_qty,
                )

                open_cost[token] = max(
                    0,
                    open_cost[token] - cost_basis,
                )

            trades.append({
                "signature": tx.get("signature"),
                "side": "SELL",
                "asset": token,
                "qty": qty_sold,
                "proceeds": received_amount,
                "proceeds_asset": received_asset,
                "timestamp": tx.get("timestamp"),
            })

    closed_trades = wins + losses

    win_rate = (
        (wins / closed_trades) * 100
        if closed_trades
        else None
    )

    activity = min(
        100,
        len(swaps) * 2,
    )

    pnl_score = (
        max(
            0,
            min(
                100,
                50 + pnl * 10,
            ),
        )
        if closed_trades
        else 50
    )

    win_score = (
        win_rate
        if win_rate is not None
        else 50
    )

    score = round(
        0.45 * pnl_score
        + 0.35 * win_score
        + 0.20 * activity,
        1,
    )

    return {
        "address": address,
        "research_score": score,
        "transactions_analyzed": len(txs),
        "swap_count": len(swaps),
        "buy_events": buys,
        "sell_events": sells,
        "realized_pnl_sol": round(pnl, 6),
        "closed_trades": closed_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": (
            round(win_rate, 1)
            if win_rate is not None
            else None
        ),
        "data_source": "Helius Enhanced Transactions",
        "trades": trades[-50:],
        "note": (
            "PnL is realized SOL PnL from "
            "successfully decoded swaps."
        ),
    }


def key_pub(key):
    return (
        key.get("pubkey")
        if isinstance(key, dict)
        else key
    )


def fallback(rpc, address, limit):
    signatures = rpc.call(
        "getSignaturesForAddress",
        [
            address,
            {
                "limit": limit,
                "commitment": "confirmed",
            },
        ],
    ) or []

    valid = [
        s
        for s in signatures
        if not s.get("err")
    ]

    requests_batch = [
        {
            "method": "getTransaction",
            "params": [
                s["signature"],
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        }
        for s in valid
    ]

    results = rpc.batch(requests_batch)

    sol_changes = []

    for signature, result in zip(
        valid,
        results,
    ):
        tx = (
            result.get("result")
            if isinstance(result, dict)
            else None
        )

        if not tx:
            continue

        meta = tx.get("meta") or {}

        keys = (
            tx.get("transaction", {})
            .get("message", {})
            .get("accountKeys", [])
        )

        index = next(
            (
                i
                for i, key in enumerate(keys)
                if key_pub(key) == address
            ),
            None,
        )

        if (
            index is not None
            and index < len(
                meta.get("preBalances", [])
            )
            and index < len(
                meta.get("postBalances", [])
            )
        ):
            change = (
                meta["postBalances"][index]
                - meta["preBalances"][index]
            ) / 1e9

            sol_changes.append(change)

    activity = min(
        100,
        len(valid) * 2,
    )

    consistency = 50

    if sol_changes:
        average = (
            sum(abs(x) for x in sol_changes)
            / max(1, len(sol_changes))
        )

        consistency = max(
            0,
            100 - min(
                100,
                average * 20,
            ),
        )

    return {
        "address": address,
        "research_score": round(
            0.6 * activity
            + 0.4 * consistency,
            1,
        ),
        "transactions_analyzed": len(valid),
        "swap_count": 0,
        "buy_events": 0,
        "sell_events": 0,
        "realized_pnl_sol": None,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "data_source": "Solana RPC fallback",
        "trades": [],
        "note": (
            "HELIUS_API_KEY is required "
            "for swap-based PnL and win rate."
        ),
    }


def main():
    config = json.loads(
        CFG.read_text()
    )

    discovery = json.loads(
        IN.read_text()
    )

    rpc = SolanaRPC(
        os.getenv(
            "SOLANA_RPC_URL",
            "https://api.mainnet-beta.solana.com",
        )
    )

    max_candidates = int(
        config["settings"][
            "max_candidates_for_deep_scan"
        ]
    )

    history_limit = int(
        config["settings"][
            "candidate_history_signatures"
        ]
    )

    ranked = []

    candidates = discovery.get(
        "candidates",
        [],
    )[:max_candidates]

    print(
        "Analysis mode:",
        "Helius"
        if HELIUS_KEY
        else "RPC fallback",
    )

    for candidate in candidates:
        address = candidate["address"]

        try:
            if HELIUS_KEY:
                result = analyze_helius(
                    address,
                    history_limit,
                )
            else:
                result = fallback(
                    rpc,
                    address,
                    history_limit,
                )

            result["discovery_score"] = candidate[
                "discovery_score"
            ]

            result["protocol_count"] = candidate[
                "protocol_count"
            ]

            result["program_activity"] = candidate[
                "program_activity"
            ]

            ranked.append(result)

        except Exception as error:
            print(
                "candidate failed",
                address,
                error,
            )

    ranked.sort(
        key=lambda x: (
            x.get("realized_pnl_sol") is not None,
            x["research_score"],
        ),
        reverse=True,
    )

    OUT.write_text(
        json.dumps(
            {
                "generated_at": int(time.time()),
                "wallets": ranked,
            },
            indent=2,
        )
    )

    print(
        f"Analyzed {len(ranked)} discovered wallets "
        f"using "
        f"{'Helius' if HELIUS_KEY else 'RPC fallback'}"
    )


if __name__ == "__main__":
    main()
