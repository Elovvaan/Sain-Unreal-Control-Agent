# UnrealMCP Plugin Install

1. Copy this folder into your Unreal project:
   - `YourProject/Plugins/UnrealMCP/`
2. Reopen Unreal Engine.
3. Go to **Edit → Plugins** and confirm **Unreal MCP Bridge** is enabled.
4. The bridge auto-starts on editor launch (module loading phase: `PostEngineInit`).
5. Verify health endpoint:

```bash
curl -i http://127.0.0.1:8765/health
```

Expected status line includes `HTTP/1.1 200` and body similar to:

```json
{"status":"ok","plugin":"UnrealMCP"}
```
