"""Reproducible local benchmark; never reads production profiles or SSH secrets."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--iterations', type=int, default=80)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='mxioc-benchmark-') as directory:
        root = Path(directory)
        server.ROOT, server.PROFILES_FILE, server.PROFILES_DIR = root, root/'profiles.json', root/'profiles'
        server.FILES, server.PROFILES_CACHE, server.PROFILES_MTIME = [root/'clash.yaml'], None, 0
        server.CONFIG_CACHE = {}
        doc = server.minimal_profile_config()
        doc['proxies'] = [{'name': f'node-{i}', 'type': 'vless', 'server': '198.51.100.1', 'port': 443,
                          'uuid': '22453845-ff1a-471d-8214-25952a9348a6'} for i in range(1000)]
        doc['rules'] = [f'DOMAIN-SUFFIX,site-{i}.example.com,DIRECT' for i in range(10000)]
        server.FILES[0].write_text(server.yaml_dump(doc))
        server.load_profiles()
        def response():
            return server.snapshot_json() if hasattr(server, 'snapshot_json') else json.dumps(server.snapshot(), ensure_ascii=False).encode()
        response()
        results=[]
        for _ in range(args.iterations):
            start=time.perf_counter()
            body=response()
            results.append((time.perf_counter()-start)*1000)
        ordered=sorted(results)
        print(json.dumps({'nodes':1000,'rules':10000,'iterations':args.iterations,
              'median_ms':round(statistics.median(results),3),'p95_ms':round(ordered[int(.95*(len(ordered)-1))],3),
              'response_bytes':len(body),
              'gzip_bytes':len(server.snapshot_json(True)) if hasattr(server,'snapshot_json') else None},indent=2))


if __name__ == '__main__':
    main()
