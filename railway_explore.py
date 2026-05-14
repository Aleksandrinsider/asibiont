#!/usr/bin/env python3
"""Railway API introspect Log type"""
import json, urllib.request

TOKEN = "1b153073-e600-4ce3-aa37-5fe570db052c"
API = "https://api.railway.app/graphql/v2"

def gql(query, variables=None):
    data = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(API, data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    return json.loads(urllib.request.urlopen(req).read().decode())

# Introspect Log type
r = gql("""
{
    __type(name: "Log") {
        name
        fields {
            name
            type { name kind }
        }
    }
}
""")
print(json.dumps(r, indent=2, ensure_ascii=False))
