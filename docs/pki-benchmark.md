# Synthetic PKI benchmark

`scripts/benchmark-pki.py` is an opt-in, local benchmark. It uses the existing
test CA fixture, freshly signed synthetic profiles and protected temporary
directories. It never reads operator PKI, contacts VPN nodes or serves a socket.
Only population sizes, repetition counts and timing medians reach stdout;
failures print a fixed message without exception details or command output.

Run from the repository root using the existing PKI test image:

```sh
docker run --rm --pull never --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount type=bind,source="$PWD/scripts/benchmark-pki.py",target=/repo/scripts/benchmark-pki.py,readonly \
  --mount type=bind,source="$PWD/web/pki",target=/repo/web/pki,readonly \
  --entrypoint python veilway-control-pki-test:0.1.0 \
  /repo/scripts/benchmark-pki.py
```

The default populations are 10, 100 and 1000 profiles, with three repetitions
per size. `--sizes 10 --repetitions 1` is available for a short smoke run. The
script can also run with existing local OpenSSL/OpenVPN tools via
`python3 scripts/benchmark-pki.py`; it does not install tools or build images.

Fixture preparation signs the population in place inside a disposable tree,
avoiding quadratic setup copies. It then protects, checks and synchronizes the
completed fixture. Each trial receives a fresh copy of that baseline. Timed
issuance and subsequent revocation use the ordinary `Store`, including locks,
generation copies, validation, signing, synchronization, `CURRENT` commit and
recovery. Revocation targets the newly issued profile, so its population contains
one more profile than the initial size. Setup and baseline restoration are not
included in operation timings. Production storage code is unchanged.

Phase instrumentation counts recursive calls only once: `copy` includes
`copytree`, `check` includes `check_tree`, and `sync` includes `sync_tree` plus
directory synchronization. Total time also includes cryptographic commands,
permission normalization, cleanup and other work. Medians are calculated
independently for each metric; phase medians need not add up to the total median.

## Reference measurement

Measured on 2026-10-09 in the network-isolated test container, with `/tmp` on
tmpfs. All values are seconds; each row is the median of three trials.

| Profiles | Operation | Total | Copy | Check | Sync |
| --- | --- | ---: | ---: | ---: | ---: |
| 10 | Issue | 0.167984 | 0.007756 | 0.006420 | 0.002412 |
| 10 | Revoke | 0.048007 | 0.006559 | 0.006242 | 0.002459 |
| 100 | Issue | 0.281316 | 0.049097 | 0.043036 | 0.014090 |
| 100 | Revoke | 0.151345 | 0.048559 | 0.038657 | 0.014006 |
| 1000 | Issue | 1.185678 | 0.400290 | 0.331835 | 0.130056 |
| 1000 | Revoke | 1.028813 | 0.311566 | 0.341927 | 0.143585 |

## Recommendation

Retain the current durable generation format and recovery algorithm for this
refactor. These measurements show increasing filesystem work as the population
grows, but tmpfs does not represent persistent-disk latency or durable `fsync`
costs. Before changing storage, repeat the benchmark on disposable protected
disk storage with a representative population and establish an operator-approved
latency target. If that target is exceeded, investigate generation copying and
repeated tree traversal first. Any optimization must separately pass crash
recovery, idempotency, symlink/hardlink rejection and permission checks; these
measurements alone do not justify weakening them.
