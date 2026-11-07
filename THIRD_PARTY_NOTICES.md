# Third-party notices

## wow-ai

HermesWoW reuses the pixel-strip codec and transport design of
[wow-ai](https://github.com/chelinho139/wow-ai) (commit `60165ca5bb6c6452f18604dc7b4fe8678b607bed`):

- `addon/HermesWoW/Codec.lua` is adapted from `addon/WoWAI/Codec.lua` (global renamed).
- `companion/hermeswow.py` (`decode_strip`, `Capture`, `SILENT_WAV`) ports the decoder of
  `bridge/capture.ps1` and the silent WAV of `bridge/protocol.js` to Python.
- The load-on-demand reply slots and the "empty vs. playable .wav" doorbells follow wow-ai's
  `docs/ARCHITECTURE.md`.

```
MIT License

Copyright (c) 2026 chelinho139

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Hermes Agent

The companion imports Hermes Agent's own client (`hermes_cli.gateway_client`) and session store
reader from your local installation at run time. No Hermes code is copied into this project.
