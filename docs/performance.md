# Backend performance and verification

Measured locally on 2026-10-22 using a temporary, generated subscription containing 1,000 nodes and 10,000 routing rules. Production files, nodes and servers are not read or modified by this benchmark. Numbers describe a warm snapshot response path, not complete HTTP latency, SSH operation latency or the entire application's throughput.

| Measurement | Before this change | After this change |
| --- | ---: | ---: |
| Median snapshot JSON generation, 80 iterations | 19.586 ms | 0.223 ms |
| p95 snapshot JSON generation | 25.916 ms | 0.466 ms |
| Uncompressed response | 1,864,863 bytes | 1,864,863 bytes |
| Gzip response | Not measured | 104,830 bytes |

Run again with `python scripts/benchmark_snapshot.py`. Timing varies by machine and load. The benchmark builds the same input shape, but each run intentionally uses a fresh temporary subscription ID.

## What changed

- Parsed configuration, derived node/group/rule views and the static JSON prefix share a configuration cache entry. Editing or reloading a changed file replaces that entry and invalidates its derived data.
- The configuration cache retains at most eight recently used files. It trades bounded memory for reduced parsing and serialization work; it is not a claim that Python's runtime has become smaller.
- Subscription usage, expiry, revocation and ownership are calculated separately for every response. They are not frozen in a cached snapshot. The gzip representation is reused only while both the configuration and dynamic profile JSON are unchanged.
- The snapshot endpoint negotiates gzip, respects an explicit `gzip;q=0`, and marks `Vary: Accept-Encoding`. Error responses remain JSON and authorization still runs first.
- Hardware monitoring uses a separate `server_monitor.py` module. Visitors viewing the same host share a five-second sample and an in-flight SSH operation. Failed samples are also backed off for five seconds.
- Monitoring stops polling in a background tab or after navigation, cancels stale requests, retains at most 60 trend samples per selected host/interface, and discards old histories after 64 combinations.

## Why the backend remains Python

The measured repeated transformation and JSON serialization work can be eliminated without replacing the application. SSH handshakes and remote commands remain network operations; changing their implementation language does not remove their round trips. A full language rewrite would also need to revalidate subscription conversion, encrypted credentials, panel compatibility and quota enforcement. This change keeps those interfaces and improves the measured hot path first. It does not claim that every backend bottleneck has been profiled or that another language can never help.

## Validation

- Python regression tests, native Xray/sing-box configuration checks, and Linux `/proc` collector execution.
- HTTP tests covering gzip negotiation, live usage/expiry updates and cache invalidation across configurations.
- Monitor tests covering first samples, counter resets, reboot, long gaps, stale failures, failure backoff and concurrent visitors.
- Real Chromium UI checks using mock API data: 42 page/theme combinations, the three node dialogs, node validation preview, 390-pixel layout, 22-pixel Lucide navigation icons and no old dark control backgrounds in the pink theme.

The screenshots and browser checks use demonstration data. They do not verify connectivity or hardware statistics from a production SSH server.

## Node editing (2026-10-23)

A separate benchmark now measures an actual node PUT with 1,000 nodes and 10,000 rules. Each iteration alternates one port, validates the resulting document, creates a backup and atomically replaces the file. It excludes HTTP transfer, SSH, panel APIs and core restarts.

| Local warmed save, 30 iterations | Before | After |
| --- | ---: | ---: |
| Median | 61.960 ms | 27.275 ms |
| p95 | 68.172 ms | 32.400 ms |

A subsequent 80-iteration run measured median 28.714 ms and p95 37.370 ms under concurrent test load. Run `python scripts/benchmark_node_edit.py --iterations 80` to reproduce. Results vary by host and filesystem.

Same-name edits copy only the node list and changed node. Unchanged top-level YAML sections reuse serialized fragments after the first edit; backups, validation, permissions and atomic replacement remain. The cache owns immutable document branches, and normal readers still receive deep copies. Renames and deletes use the complete reference-update path. Node mutations serialize their read/modify/write sequence, preventing simultaneous node edits from overwriting each other. Identical node PUTs do not rewrite the file, create backups or reload the snapshot in the browser. Tests cover concurrent edits, old-cache immutability, YAML aliases/shared values and Reality short IDs.

This does not measure or claim faster remote SSH operations or remote core restarts.
