import json, os, re, time
from pathlib import Path
from collections import defaultdict
from statistics import mean
import requests
from solana_rpc import SolanaRPC

ROOT=Path(__file__).resolve().parents[1]
CFG=ROOT/"config/programs.json"; IN=ROOT/"data/discovery.json"; OUT=ROOT/"data/rankings.json"
HELIUS_KEY=os.getenv("HELIUS_API_KEY","").strip()
HELIUS_BASE="https://api.helius.xyz/v0/addresses/{address}/transactions"

def parse_amount(s):
    if not s: return None
    m=re.search(r'([0-9][0-9,]*(?:\\.[0-9]+)?)\\s*(SOL|USDC|USDT|[A-Z0-9]{2,12})', s, re.I)
    return (float(m.group(1).replace(',','')), m.group(2).upper()) if m else None

def helius_history(address, limit):
    if not HELIUS_KEY: return []
    url=HELIUS_BASE.format(address=address)
    out=[]; before=None
    while len(out)<limit:
        params={"api-key":HELIUS_KEY,"limit":min(100,limit-len(out))}
        if before: params["before-signature"]=before
        r=requests.get(url,params=params,timeout=45); r.raise_for_status()
        page=r.json() or []
        if not page: break
        out.extend(page); before=page[-1].get("signature")
        if len(page)<100: break
    return out[:limit]

def analyze_helius(address, limit):
    txs=helius_history(address,limit)
    swaps=[t for t in txs if t.get("type")=="SWAP"]
    buys=sells=0; pnl=0.0; open_cost=defaultdict(float); open_qty=defaultdict(float); wins=losses=0
    trades=[]
    for t in reversed(swaps):
        desc=t.get("description","")
        # Helius descriptions commonly look like "Swapped X SOL for Y TOKEN".
        m=re.search(r'Swapped\\s+(.+?)\\s+for\\s+(.+?)(?:\\s+via\\s+|$)',desc,re.I)
        if not m: continue
        give=parse_amount(m.group(1)); get=parse_amount(m.group(2))
        if not give or not get: continue
        ga,gt=give; ra,rt=get
        # Treat SOL/stablecoin spent as cost; token received as position. Reverse direction closes position.
        if gt in {"SOL","USDC","USDT"} and rt not in {"SOL","USDC","USDT"}:
            buys+=1; open_qty[rt]+=ra; open_cost[rt]+=ga
            trades.append({"signature":t.get("signature"),"side":"BUY","asset":rt,"qty":ra,"cost_sol":ga,"timestamp":t.get("timestamp")})
        elif rt in {"SOL","USDC","USDT"} and gt not in {"SOL","USDC","USDT"}:
            sells+=1; qty=ga; mint=gt
            avg=open_cost[mint]/open_qty[mint] if open_qty[mint]>0 else None
            if avg is not None:
                realized=rt if rt=="SOL" else 0.0
                if rt=="SOL":
                    cost=avg*qty; p=ra-cost; pnl+=p
                    if p>0: wins+=1
                    elif p<0: losses+=1
                open_qty[mint]=max(0,open_qty[mint]-qty); open_cost[mint]=max(0,open_cost[mint]-avg*min(qty,open_qty[mint]+qty))
            trades.append({"signature":t.get("signature"),"side":"SELL","asset":mint,"qty":qty,"proceeds":ra,"proceeds_asset":rt,"timestamp":t.get("timestamp")})
    closed=wins+losses
    win_rate=(wins/closed*100) if closed else None
    activity=min(100,len(swaps)*2)
    pnl_score=max(0,min(100,50+pnl*10)) if closed else 50
    win_score=win_rate if win_rate is not None else 50
    score=round(.45*pnl_score+.35*win_score+.20*activity,1)
    return {"address":address,"research_score":score,"transactions_analyzed":len(txs),"swap_count":len(swaps),"buy_events":buys,"sell_events":sells,"realized_pnl_sol":round(pnl,6),"closed_trades":closed,"win_rate":round(win_rate,1) if win_rate is not None else None,"data_source":"Helius Enhanced Transactions","trades":trades[-50:],"note":"PnL is only realized SOL PnL from swaps that Helius descriptions could unambiguously parse; stablecoin exits and unpriced transfers are excluded."}

def key_pub(k): return k.get("pubkey") if isinstance(k,dict) else k

def fallback(rpc,address,limit):
    sigs=rpc.call("getSignaturesForAddress",[address,{"limit":limit,"commitment":"confirmed"}]) or []
    valid=[s for s in sigs if not s.get("err")]
    req=[{"method":"getTransaction","params":[s["signature"],{"encoding":"jsonParsed","commitment":"confirmed","maxSupportedTransactionVersion":0}]} for s in valid]
    results=rpc.batch(req); sol=[]
    for s,res in zip(valid,results):
        tx=res.get("result") if isinstance(res,dict) else None
        if not tx: continue
        meta=tx.get("meta") or {}; keys=tx.get("transaction",{}).get("message",{}).get("accountKeys",[])
        idx=next((i for i,k in enumerate(keys) if key_pub(k)==address),None)
        if idx is not None and idx<len(meta.get("preBalances",[])) and idx<len(meta.get("postBalances",[])):
            sol.append((meta["postBalances"][idx]-meta["preBalances"][idx])/1e9)
    activity=min(100,len(valid)*2); consistency=50
    if sol:
        avg=sum(abs(x) for x in sol)/max(1,len(sol)); consistency=max(0,100-min(100,avg*20))
    return {"address":address,"research_score":round(.6*activity+.4*consistency,1),"transactions_analyzed":len(valid),"swap_count":0,"buy_events":0,"sell_events":0,"realized_pnl_sol":None,"closed_trades":0,"win_rate":None,"data_source":"Solana RPC fallback","trades":[],"note":"Set HELIUS_API_KEY for real swap decoding and realized-PnL analysis."}

def main():
    cfg=json.loads(CFG.read_text()); disc=json.loads(IN.read_text())
    rpc=SolanaRPC(os.getenv("SOLANA_RPC_URL","https://api.mainnet-beta.solana.com"))
    n=int(cfg["settings"]["max_candidates_for_deep_scan"]); limit=int(cfg["settings"]["candidate_history_signatures"])
    ranked=[]
    for c in disc.get("candidates",[])[:n]:
        try: x=analyze_helius(c["address"],limit) if HELIUS_KEY else fallback(rpc,c["address"],limit)
        except Exception as e:
            print("candidate failed",c["address"],e); continue
        x["discovery_score"]=c["discovery_score"]; x["protocol_count"]=c["protocol_count"]; x["program_activity"]=c["program_activity"]; ranked.append(x)
    ranked.sort(key=lambda x:((x.get("realized_pnl_sol") is not None),x["research_score"]),reverse=True)
    OUT.write_text(json.dumps({"generated_at":int(time.time()),"wallets":ranked},indent=2))
    print(f"Analyzed {len(ranked)} discovered wallets using {'Helius' if HELIUS_KEY else 'RPC fallback'}")
if __name__=="__main__": main()
