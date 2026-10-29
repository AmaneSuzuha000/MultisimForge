# Third-party notices

This repository is distributed under the MIT license (see `LICENSE`).

## MIT

`Copyright (c) 2026 1naruto-1`
`Copyright (c) 2026 Multisim MCP contributors`

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

## PKWare DCL tables

The Huffman tables in `multisimforge/codec.py` are the fixed constants of the PKWare Data Compression Library (implode/explode) format, the same tables used by StormLib's pklib implementation. The encoder and decoder here are original implementations.

## Runtime dependencies (not distributed here)

- [electronics-workbench-decoder](https://github.com/cinderblock/electronics-workbench-decoder) (ISC): called through Node.js only by `parts dbindex` to read the Multisim master component database index.
- NI Multisim 14: the part template library is generated locally from the Multisim samples and database licensed on the user's machine, stored in `%LOCALAPPDATA%\multisimforge\parts`. It is not part of this repository and must not be redistributed.
