#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Railway logs fetcher — writes output to UTF-8 file to avoid cp1251 issues"""

import json, sys, urllib.request, urllib.error, re

TOKEN = "1b153073-e600-4ce3-aa37-5fe570db052c"
API = "https://api.railway.app/graphql/v2"
PROJECT_ID = "512ebdac-3f17-47e6-b24c-7ff816946327"
ENV_ID = "daaec95f-2be2-4085-92a6-e65e9e170dcb"

def gql(query, variables=None):
    data = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(API, data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:2000]
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        return None

OUTPUT_FILE = "railway_logs_output.txt"

def save_logs(dep_id, limit=500):
    q = """
    query($deploymentId: String!, $limit: Int) {
        deploymentLogs(deploymentId: $deploymentId, limit: $limit) {
            message
            timestamp
            severity
        }
    }
    """
    r = gql(q, {"deploymentId": dep_id, "limit": limit})
    if not r:
        return
    logs = r.get("data", {}).get("deploymentLogs", [])
    if not logs:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(f"No logs. Response: {json.dumps(r, indent=2)[:500]}")
        return
    
    lines = []
    lines.append(f"=== Deployment logs ({len(logs)} entries) ===\n")
    for log in logs:
        ts = log.get("timestamp", "")
        msg = log.get("message", "") or ""
        sev = log.get("severity", "")
        # Try to extract inner message from JSON-wrapped logs
        if isinstance(msg, str) and msg.startswith("{"):
            try:
                parsed = json.loads(msg)
                if isinstance(parsed, dict):
                    msg = parsed.get("message", msg)
            except:
                pass
        lines.append(f"[{ts}] [{sev}] {msg}")
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    
    print(f"Saved {len(logs)} log entries to {OUTPUT_FILE}")

def search_logs(dep_id, pattern, limit=500):
    q = """
    query($deploymentId: String!, $limit: Int) {
        deploymentLogs(deploymentId: $deploymentId, limit: $limit) {
            message
            timestamp
            severity
        }
    }
    """
    r = gql(q, {"deploymentId": dep_id, "limit": limit})
    if not r:
        return
    logs = r.get("data", {}).get("deploymentLogs", [])
    if not logs:
        print("No logs found")
        return
    
    pat = re.compile(pattern, re.IGNORECASE)
    found = 0
    lines = []
    for log in logs:
        msg = log.get("message", "") or ""
        ts = log.get("timestamp", "")
        sev = log.get("severity", "")
        if isinstance(msg, str) and msg.startswith("{"):
            try:
                parsed = json.loads(msg)
                if isinstance(parsed, dict):
                    msg = parsed.get("message", msg)
            except:
                pass
        if pat.search(str(msg)):
            found += 1
            lines.append(f"[{ts}] [{sev}] {msg}")
    
    output = "\n".join(lines) + f"\n\n--- Found {found} matching lines ---"
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(output)
    print(f"Found {found} matches saved to {OUTPUT_FILE}")

def list_deployments():
    q = """
    query($projectId: String!, $envId: String) {
        deployments(input: {projectId: $projectId, environmentId: $envId}, first: 5) {
            edges {
                node { id status createdAt }
            }
        }
    }
    """
    r = gql(q, {"projectId": PROJECT_ID, "envId": ENV_ID})
    deps = (r or {}).get("data", {}).get("deployments", {}).get("edges", [])
    if not deps:
        print("No deployments")
        return []
    print(f"{'Deployment ID':44s} {'Status':12s} {'Created'}")
    print("-" * 80)
    ids = []
    for d in deps:
        n = d["node"]
        print(f"{n['id']:44s} {n['status']:12s} {n.get('createdAt','')}")
        ids.append(n["id"])
    return ids

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "deps" or cmd == "deployments":
        list_deployments()
    elif cmd == "logs":
        dep_id = sys.argv[2] if len(sys.argv) > 2 else None
        if not dep_id:
            ids = list_deployments()
            if ids:
                dep_id = ids[0]
        if dep_id:
            limit = int(sys.argv[3]) if len(sys.argv) > 3 else 500
            save_logs(dep_id, limit)
    elif cmd == "search":
        pattern = sys.argv[2] if len(sys.argv) > 2 else "ERROR|WARNING"
        dep_id = sys.argv[3] if len(sys.argv) > 3 else None
        if not dep_id:
            ids = list_deployments()
            if ids:
                dep_id = ids[0]
        if dep_id:
            limit = int(sys.argv[4]) if len(sys.argv) > 4 else 500
            search_logs(dep_id, pattern, limit)
    else:
        print("Commands: deps, logs [dep_id] [limit], search <pattern> [dep_id] [limit]")

if __name__ == "__main__":
    main()
