import sys, json, time, datetime, logging
from pathlib import Path

ROOT = Path(__file__).parent
QUOTA_FILE = ROOT / "quota.json"

def format_relative_time(target_iso):
    try:
        dt = datetime.datetime.fromisoformat(target_iso.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        diff = dt - now
        secs = int(diff.total_seconds())
        if secs <= 0:
            return "now"
        days = secs // 86400
        hours = (secs % 86400) // 3600
        mins = (secs % 3600) // 60
        if days > 0:
            return f"{days}d {hours}h"
        elif hours > 0:
            return f"{hours}h {mins}m"
        else:
            return f"{mins}m"
    except Exception:
        return target_iso

def update_quota_file():
    from antigravity_pro_bridge import rpc
    try:
        # 1. Try RetrieveUserQuotaSummary which has both 5h and weekly quotas
        summary_resp = rpc("RetrieveUserQuotaSummary").get("response", {})
        groups = summary_resp.get("groups", [])
        if groups:
            lines = []
            structured_groups = []
            for g in groups:
                raw_name = g.get("displayName", "")
                title = "Gemini" if "Gemini" in raw_name else "Claude/GPT" if "Claude" in raw_name else raw_name
                order = {"5h": 0, "weekly": 1}
                sorted_buckets = sorted(g.get("buckets", []), key=lambda b: order.get(b.get("window"), 99))
                bucket_strs = []
                buckets_data = []
                for b in sorted_buckets:
                    w = b.get("window")
                    label = "5h额度" if w == "5h" else "周额度" if w == "weekly" else b.get("displayName", "")
                    pct = round(b.get("remainingFraction", 0) * 100, 1)
                    reset_iso = b.get("resetTime", "")
                    reset = format_relative_time(reset_iso)
                    reset_str = f"({reset})" if reset else ""
                    bucket_strs.append(f"{label} {pct}% {reset_str}".strip())
                    buckets_data.append({
                        "window": w,
                        "label": label,
                        "percent": pct,
                        "resetIn": reset,
                        "resetTime": reset_iso
                    })
                lines.append(f"  • {title}: " + " · ".join(bucket_strs))
                structured_groups.append({
                    "title": title,
                    "buckets": buckets_data
                })

            formatted_line = "💳 Quota:\n" + "\n".join(lines)
            payload = {
                "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "formattedLine": formatted_line,
                "groups": structured_groups
            }
            QUOTA_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            return payload

        # 2. Fallback to GetUserStatus
        u = rpc("GetUserStatus").get("userStatus", {})
        tier_name = u.get("userTier", {}).get("name") or "Google AI Pro"
        email = u.get("email") or ""
        configs = u.get("cascadeModelConfigData", {}).get("clientModelConfigs", [])
        
        seen_quotas = {}
        for m in configs:
            if m.get("disabled"):
                continue
            q = m.get("quotaInfo", {})
            frac = q.get("remainingFraction")
            reset = q.get("resetTime")
            if frac is None:
                continue
            key = (round(frac, 4), reset)
            if key not in seen_quotas:
                label = m.get("label", "")
                if "Gemini" in label:
                    short_name = "Gemini Flash"
                elif "Claude" in label:
                    short_name = "Claude"
                elif "GPT" in label:
                    short_name = "GPT-OSS"
                else:
                    short_name = label.split()[0]
                seen_quotas[key] = {
                    "name": short_name,
                    "percent": round(frac * 100, 1),
                    "resetIn": format_relative_time(reset) if reset else "",
                    "resetTime": reset
                }
        
        parts = []
        for q in seen_quotas.values():
            reset_str = f" ({q['resetIn']})" if q["resetIn"] else ""
            parts.append(f"{q['name']} {q['percent']}%{reset_str}")
        
        formatted_line = "💳 Quota: " + " · ".join(parts)
        payload = {
            "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tier": tier_name,
            "email": email,
            "formattedLine": formatted_line,
            "models": list(seen_quotas.values())
        }
        QUOTA_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        return payload
    except Exception as e:
        logging.warning("Failed to update quota file: %s", e)
        return None

if __name__ == "__main__":
    res = update_quota_file()
    print(json.dumps(res, indent=2, ensure_ascii=False))
