#!/bin/bash
set -euxo pipefail

source .venv/bin/activate

# python3 non-itl/parse_song_data.py

for sort in jumps j2j j10j avg_nps; do
    for level in 1 2 3 4 5; do
        python3 non-itl/generate-itg-playlist.py --min-length 2:00 --max-length 5:00 --min-block $level --max-block $level --sort $sort
    done
    python3 non-itl/generate-itg-playlist.py --min-length 2:00 --max-length 5:00 --min-block 1 --max-block 5 --sort $sort
done

python3 non-itl/generate-itg-playlist.py --min-length 5:00 --min-block 7 --max-block 10 --sort length
