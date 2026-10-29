# Security policy

## Scope

MultisimForge drives a locally installed, licensed copy of NI Multisim through its COM
automation interface, and generates/reads `.ms14` files on the local disk.

It does **not**:

- send any data over the network (the COM worker uses a loopback socket only),
- store or transmit credentials,
- execute code from a design file.

## Things worth knowing

- `build` writes to the path given in `spec["output"]`. Point it at a scratch
  directory when processing untrusted specs.
- `.ms14` files are parsed as XML. Only open designs whose origin you trust;
  malformed input raises an exception rather than being silently repaired.
- `parts fetch` drives a real Multisim process and can crash it on an
  incompatible carrier model. Leftover processes are cleaned up, but save your
  work first.
- The background COM worker listens on `127.0.0.1` with a random port and a
  per-run token; it refuses requests without the token and exits after
  `com_idle_timeout` seconds of inactivity.

## Reporting

Open a private security advisory on this repository (Security → Advisories) or
contact the maintainers directly. Please do not open a public issue for
anything that could be exploited.

Include: affected version or commit, a minimal reproduction, and the Multisim
version you ran it against.