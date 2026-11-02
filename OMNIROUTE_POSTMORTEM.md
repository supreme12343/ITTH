# OmniRoute and Blender Agent Engineering Post-Mortem and Troubleshooting Guide

**Document status:** Formal incident analysis and recovery runbook  
**Scope:** Local OmniRoute service on `127.0.0.1:20128`, the Python `blender_agent.py` client, and Gemini swarm failover  
**Incident symptom:** TCP port `20128` accepted connections, but OmniRoute HTTP requests to both documented chat paths timed out without returning an HTTP response.

## 1. Executive Summary

The Blender agent was configured to call OmniRoute using an OpenAI-compatible Chat Completions request. Investigation confirmed that a process was listening on port `20128`, but requests to both:

- `http://127.0.0.1:20128/v1/chat/completions`
- `http://127.0.0.1:20128/api/v1/vscode/{token}/chat/completions`

failed by read timeout rather than returning a normal HTTP status. This distinguishes a reachable TCP listener from a healthy application service.

The client was updated to perform a fast TCP availability check, apply separate connection and read timeouts, try the standard endpoint and VS Code endpoint, parse OpenAI-compatible responses defensively, and fail over to the Gemini key/model swarm when OmniRoute is unavailable or non-responsive.

The service must still be restarted and verified independently. A listening port alone is not sufficient evidence that OmniRoute is healthy.

## 2. Incident Impact

- Blender scene-generation requests could block for an extended period while waiting for OmniRoute.
- The user-facing agent could not reliably distinguish a dead service from a slow model response.
- The standard and VS Code endpoint variants were both tested, but neither produced an HTTP response during the incident.
- Gemini fallback remained available in the client, but its success depends on the `google-genai` dependency and valid Gemini credentials.

## 3. Root Cause Analysis

### 3.1 Node.js event-loop deadlock or stalled request handler

The listener on port `20128` was owned by a Node.js process. The TCP handshake completed, proving that the process had bound the port. However, HTTP requests did not receive headers or a response body before the client timeout.

This pattern is consistent with a Node.js application whose event loop or request path is stalled, for example:

- A synchronous CPU-bound operation blocks the event loop.
- A promise or callback in the proxy pipeline never resolves.
- An upstream provider request has no effective timeout and leaves the handler pending.
- A stream is not closed or drained, preventing the response from completing.
- An internal queue, socket, or retry loop deadlocks while the HTTP server remains technically alive.

This is not the same as `ECONNREFUSED`: the process was accepting TCP connections, but the application layer was not completing requests. The exact internal deadlock requires OmniRoute process logs or source-level diagnostics and cannot be proven from the client side alone.

**Diagnostic indicator:** `Test-NetConnection` or a raw socket connection succeeds, while `curl --max-time ...` hangs until its read deadline.

### 3.2 Authorization errors

OmniRoute exposes both a conventional OpenAI-compatible path and a VS Code token-alias path. Authorization must match the selected route:

- Standard route: bearer authorization is normally supplied in the `Authorization` header.
- VS Code route: the token is embedded in `/api/v1/vscode/{token}/...`; the bearer header may still be accepted, but the path token is the important route identifier.

A bad token normally produces `401` or `403`. It should not produce an indefinite timeout. Therefore, authorization was a possible configuration risk but was not the primary observed failure in this incident because no HTTP status was returned.

If a service returns `401` or `403`, correct the hardcoded token and verify that the token is not accidentally truncated, URL-decoded incorrectly, or sent to the wrong endpoint family.

### 3.3 Endpoint mismatch

The available endpoint documentation identifies:

- Standard OpenAI-compatible Chat Completions: `/v1/chat/completions`
- VS Code chat route: `/api/v1/vscode/{token}/chat/completions`

The client previously relied primarily on the standard route. The updated Blender agent explicitly knows both routes and tries them independently. A route mismatch usually returns `404`, `405`, or `401`; a timeout indicates the server failed to process the request after accepting it. Both conditions must nevertheless be tested separately because a working service may support only one endpoint family or may require a different token convention.

## 4. Service Recovery

### 4.1 Identify the process owning port 20128

Run PowerShell as the user who started OmniRoute:

```powershell
Get-NetTCPConnection -LocalPort 20128 -State Listen |
  Select-Object LocalAddress,LocalPort,OwningProcess

Get-Process -Id <PID> | Select-Object Id,ProcessName,Path,StartTime
Get-CimInstance Win32_Process -Filter "ProcessId = <PID>" |
  Select-Object ProcessId,CommandLine
```

Record the command line before terminating the process. It identifies how OmniRoute should be restarted.

### 4.2 Gracefully terminate the frozen process

```powershell
Stop-Process -Id <PID> -Force
```

If the process is a child of a wrapper or launcher and remains present:

```powershell
taskkill /PID <PID> /T /F
```

Confirm that the port is released:

```powershell
Get-NetTCPConnection -LocalPort 20128 -ErrorAction SilentlyContinue
```

No output means that no current TCP listener was found on that port. Do not kill unrelated Node.js processes; use the recorded PID and command line.

### 4.3 Restart OmniRoute

Restart it using the original installation or launcher command recorded in section 4.1. Keep the process attached to a visible terminal initially so startup errors are observable. If OmniRoute is launched by a VS Code extension, restart or reload the extension host and ensure the extension is active.

Do not treat a successful process launch as sufficient. Wait for the service to bind port `20128`, then perform the HTTP checks below.

### 4.4 Verify the TCP listener

```powershell
Test-NetConnection 127.0.0.1 -Port 20128
```

Expected result:

```text
TcpTestSucceeded : True
```

This verifies only the transport layer.

### 4.5 Verify the standard OpenAI-compatible endpoint with cURL

PowerShell command using `curl.exe`:

```powershell
curl.exe --noproxy "*" --max-time 15 `
  -H "Authorization: Bearer [REDACTED]" `
  -H "Content-Type: application/json" `
  -d '{"model":"auto/coding","messages":[{"role":"user","content":"Reply with exactly OK"}],"temperature":0,"max_tokens":8}' `
  http://127.0.0.1:20128/v1/chat/completions
```

Expected result: HTTP `200` with JSON containing `choices[0].message.content`. A timeout means the listener is still not healthy. A `401` or `403` is an authorization problem. A `404` or `405` is an endpoint or method mismatch.

### 4.6 Verify the VS Code token-alias endpoint with cURL

```powershell
curl.exe --noproxy "*" --max-time 15 `
  -H "Authorization: Bearer [REDACTED]" `
  -H "Content-Type: application/json" `
  -d '{"model":"auto/coding","messages":[{"role":"user","content":"Reply with exactly OK"}],"temperature":0,"max_tokens":8}' `
  http://127.0.0.1:20128/api/v1/vscode/[REDACTED]/chat/completions
```

At least one documented route must return a valid OpenAI-style response before OmniRoute is considered recovered.

## 5. Client-Side Implementation

The relevant implementation is in `blender_agent.py:536-693`.

### 5.1 Fast health check

`omniroute_available()` opens a TCP connection to `127.0.0.1:20128` with a short `0.5` second timeout. This prevents the client from issuing a full generation request when the service is not listening. The check is intentionally transport-level; the cURL procedures in this guide remain necessary for application-level health verification.

### 5.2 Endpoint selection and fallback

`call_omniroute()` builds one OpenAI-compatible payload and tries the hardcoded standard endpoint followed by the VS Code token-alias endpoint. Duplicate URLs are removed before attempting requests. This handles installations where only one endpoint family is active.

### 5.3 Fail-fast timeout behavior

The request uses separate timeouts:

- `OMNIROUTE_CONNECT_TIMEOUT = 1.5`: maximum time to establish the HTTP connection.
- `OMNIROUTE_READ_TIMEOUT = 30.0`: maximum time waiting for response data.
- `OMNIROUTE_RTY = 0`: no repeated wait for a frozen endpoint; the client proceeds to the alternate route and then Gemini.

This is critical for a local proxy that accepts TCP connections but deadlocks before returning HTTP headers. Without a read timeout, the Blender UI worker could remain blocked indefinitely.

### 5.4 Defensive response parsing

The client validates HTTP status, decodes JSON, checks that `choices` and `message.content` exist, and handles content returned as either a string or a list of text parts. Empty responses are treated as failures rather than successful generations.

Errors from both endpoints are aggregated into one diagnostic exception. `call_ai()` catches that exception, records an OmniRoute fallback event, and invokes `call_gemini()`.

### 5.5 Gemini swarm failover

`call_gemini()` iterates through the hardcoded Gemini keys in randomized order and tries each configured model. Rate-limit/resource-exhaustion errors continue to the next model or key. A successful response is returned to the Blender generation loop with engine metadata, while OmniRoute failure remains visible in the execution trail.

Gemini failover is therefore application-level resilience, not a repair for OmniRoute. It requires the `google-genai` package, valid keys, network access to Google APIs, and model identifiers accepted by the installed API version.

## 6. Troubleshooting Matrix

| Symptom | Likely cause | Action |
|---|---|---|
| `TcpTestSucceeded: False` | Service stopped or wrong port | Start OmniRoute and verify port `20128`. |
| TCP succeeds, cURL times out | Node event-loop stall, deadlocked proxy, or hung upstream request | Kill the owning process, restart OmniRoute, inspect logs. |
| HTTP `401`/`403` | Invalid token or token sent to wrong route | Correct the hardcoded token and retest the matching route. |
| HTTP `404`/`405` | Endpoint or HTTP method mismatch | Test both documented POST routes exactly. |
| HTTP `200` but no `choices` | Non-OpenAI response schema | Inspect response JSON and update the adapter only if the service contract differs. |
| OmniRoute fails and Gemini also fails | Missing dependency, invalid keys, quota, or network failure | Verify `google-genai`, keys, model availability, and API connectivity. |
| Blender socket fails after AI succeeds | BlenderMCP server not listening on port `9876` | Start Blender, enable the add-on, and start its server. |

## 7. Verification Checklist

### OmniRoute service

- [ ] The old frozen process was identified by PID and terminated.
- [ ] Only the intended OmniRoute process was restarted.
- [ ] `Test-NetConnection 127.0.0.1 -Port 20128` reports `TcpTestSucceeded : True`.
- [ ] Standard `/v1/chat/completions` cURL returns within 15 seconds.
- [ ] VS Code `/api/v1/vscode/{token}/chat/completions` cURL returns within 15 seconds, or is intentionally unsupported with a documented status.
- [ ] The successful response is HTTP `200` and contains `choices[0].message.content`.
- [ ] No `401`, `403`, `404`, `405`, or read timeout occurs on the selected route.

### Blender agent

- [ ] `python -m py_compile blender_agent.py` passes.
- [ ] `omniroute_available()` returns `True` when the service is listening.
- [ ] A normal Blender prompt records `omniroute_ok` in `logs/blender_agent_events.jsonl` when OmniRoute is healthy.
- [ ] A deliberately stopped or frozen OmniRoute request exits within the configured timeout.
- [ ] The alternate OmniRoute endpoint is attempted when the first route fails.
- [ ] The UI remains responsive while AI generation runs in its worker thread.
- [ ] A failed OmniRoute request records `omniroute_fallback`.

### Gemini swarm failover

- [ ] `google-genai` is installed in the Python environment running the agent.
- [ ] Gemini keys are present and valid.
- [ ] At least one configured Gemini model returns a response.
- [ ] The generated response contains a valid Python code fence accepted by `extract_code()`.
- [ ] The agent trail identifies the successful Gemini key/model after OmniRoute failure.
- [ ] BlenderMCP on port `9876` executes the generated code successfully.

## 8. Final Assessment

The observed incident was an application-layer health failure: OmniRoute's Node.js listener remained bound, but requests did not complete. The client-side changes correctly limit exposure to that failure and preserve service through Gemini failover. Recovery is complete only after a post-restart cURL request returns a valid OpenAI-compatible response and a Blender generation test confirms both AI and BlenderMCP execution paths.
