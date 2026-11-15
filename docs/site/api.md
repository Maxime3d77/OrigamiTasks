# HTTP API reference

The browser API lives under `/api`. Interactive sessions use `origami_session` and `origami_csrf` cookies; mutation requests must send `X-CSRF-Token` matching the CSRF cookie. Anonymous endpoints are only the health endpoint, login and authenticated webhook entry points.

| Method | Endpoint | Function | Role |
|---|---|---|---|
| GET | `/api/health` | Version/health | Public |
| POST | `/api/login` | Create session | Public |
| POST | `/api/logout` | End session | Signed-in |
| GET | `/api/me` | Current user | Signed-in |
| GET | `/api/catalog` | Available task adapters | Signed-in |
| GET | `/api/jobs` | ACL-filtered workflows | Signed-in |
| POST | `/api/jobs` | Create workflow | Admin |
| PUT | `/api/jobs/{id}` | Edit existing workflow | Authorized operator/admin |
| POST | `/api/jobs/{id}/run` | Queue execution | Authorized operator/admin |
| GET | `/api/runs` | Run history | Authorized role |
| GET | `/api/credentials` | Secret metadata only | Admin |
| POST | `/api/credentials` | Store encrypted secret | Admin |
| GET/POST | `/api/datacenters` | List/register Proxmox endpoints | Admin |
| GET | `/api/datacenters/{id}/discover` | Read Proxmox cluster inventory | Admin |
| GET | `/api/datacenters/{id}/vms/{vmid}/dr-assessment` | Read-only VM storage assessment | Admin |
| POST | `/api/updates/request` | Request Git check/apply | Admin |

Client API errors are JSON with `detail`. Audit-sensitive mutation requests should retain change-ticket and workflow context outside the application where necessary. The API is not yet a stable versioned public contract: backwards-compatible API guarantees begin at v1.0.

## Authentication example

```bash
# On the local host in a protected test environment only:
# curl -c cookies.txt -H 'Content-Type: application/json' \
#   -d '{"username":"admin","password":"..."}' https://tasks.example.org/api/login
```

Avoid passing passwords in shell history; use a secret manager or non-interactive secure input.
