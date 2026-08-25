import json, os, time
from pathlib import Path
from collections import defaultdict, Counter
from solana_rpc import SolanaRPC

ROOT=Path(__file__).resolve().parents[1]
CFG=ROOT/"config/programs.json"
OUT=ROOT/"data/discovery.json"

def pubkey(account):
    if isinstance(account, dict):
        return account.get("pubkey")
    return account

def fee_payer(tx):
    try:
        keys=tx["transaction"]["message"]["accountKeys"]
        if not keys: return None
        return pubkey(keys[0])
    except Exception:
        return None

def main():
    cfg=json.loads(CFG.read_text())
    rpc=SolanaRPC(os.getenv("SOLANA_RPC_URL","https://api.mainnet-beta.solana.com"))
    per=int(cfg["settings"]["discovery_signatures_per_program"])
    candidates=defaultdict(lambda: {"programs":Counter(),"samples":[],"signatures":set()})

    for program in cfg["programs"]:
        if not program.get("enabled"): continue
        sigs=rpc.call("getSignaturesForAddress",[program["address"],{"limit":per,"commitment":"confirmed"}]) or []
        req=[{"method":"getTransaction","params":[s["signature"],{"encoding":"jsonParsed","commitment":"confirmed","maxSupportedTransactionVersion":0}]} for s in sigs if not s.get("err")]
        results=rpc.batch(req)
        for s,res in zip([x for x in sigs if not x.get("err")], results):
            tx=res.get("result") if isinstance(res,dict) else None
            wallet=fee_payer(tx) if tx else None
            if not wallet: continue
            c=candidates[wallet]
            c["programs"][program["name"]]+=1
            c["signatures"].add(s["signature"])
            if len(c["samples"])<8:
                c["samples"].append({"signature":s["signature"],"slot":s.get("slot"),"block_time":s.get("blockTime"),"program":program["name"]})

    ranked=[]
    for address,c in candidates.items():
        activity=sum(c["programs"].values())
        diversity=len(c["programs"])
        # Discovery score rewards repeated activity and multi-protocol behavior.
        score=min(100, round(activity*1.4 + diversity*8,1))
        ranked.append({
            "address":address,
            "discovery_score":score,
            "activity_count":activity,
            "protocol_count":diversity,
            "program_activity":dict(c["programs"]),
            "samples":c["samples"]
        })
    ranked.sort(key=lambda x:(x["discovery_score"],x["activity_count"]),reverse=True)

    OUT.write_text(json.dumps({
        "generated_at":int(time.time()),
        "candidate_count":len(ranked),
        "candidates":ranked[:max(100,len(ranked))]
    },indent=2))
    print(f"Discovered {len(ranked)} candidate wallets")

if __name__=="__main__":
    main()
