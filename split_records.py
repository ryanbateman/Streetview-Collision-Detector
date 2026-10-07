#!/usr/bin/env python3
"""
Utility to split a large Google Takeout Records.json file into smaller year-based files.

This uses streaming JSON parsing (ijson) to avoid loading the entire file into memory,
which is necessary for very large location history files (1GB+).

Usage:
    python3 split_records.py
    python3 split_records.py --takeout-dir ./takeout
"""

import argparse
import ijson
import json
import os
from collections import defaultdict
from pathlib import Path


def parseArgs():
    parser = argparse.ArgumentParser(
        description="Split Records.json into year-based files using streaming JSON parsing"
    )
    parser.add_argument(
        "--takeout-dir",
        type=str,
        default="takeout",
        help="Path to takeout directory (default: ./takeout)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count records per year without writing files"
    )
    return parser.parse_args()


def getRecordsPath(takeoutDir: str) -> Path:
    """Find the Records.json file in the takeout directory."""
    # Try both possible paths
    paths = [
        Path(takeoutDir) / "Location History (Timeline)" / "Records.json",
        Path(takeoutDir) / "Location History" / "Records.json",
    ]
    for path in paths:
        if path.exists():
            return path
    raise FileNotFoundError(
        f"Records.json not found. Tried:\n  - {paths[0]}\n  - {paths[1]}"
    )


def extractYear(timestamp: str) -> str:
    """Extract year from ISO 8601 timestamp (e.g., '2024-09-09T10:06:24.341Z')."""
    return timestamp[:4]


def splitRecords(recordsPath: Path, dryRun: bool = False):
    """
    Split Records.json into year-based files using streaming parsing.
    
    Args:
        recordsPath: Path to the Records.json file
        dryRun: If True, only count records without writing files
    """
    outputDir = recordsPath.parent
    recordsByYear = defaultdict(list)
    totalRecords = 0
    
    print(f"Reading: {recordsPath}")
    print(f"File size: {recordsPath.stat().st_size / (1024**3):.2f} GB")
    print("Streaming records (this may take a few minutes)...")
    
    # Stream through the JSON file without loading it all into memory
    with open(recordsPath, "rb") as f:
        # ijson.items() yields each item in the 'locations' array one at a time
        for record in ijson.items(f, "locations.item"):
            totalRecords += 1
            
            # Extract year from timestamp
            timestamp = record.get("timestamp", "")
            if timestamp:
                year = extractYear(timestamp)
                recordsByYear[year].append(record)
            
            # Progress indicator every 100k records
            if totalRecords % 100000 == 0:
                print(f"  Processed {totalRecords:,} records...")
    
    print(f"\nTotal records: {totalRecords:,}")
    print(f"Years found: {sorted(recordsByYear.keys())}")
    print("\nRecords per year:")
    for year in sorted(recordsByYear.keys()):
        count = len(recordsByYear[year])
        print(f"  {year}: {count:,} records")
    
    if dryRun:
        print("\n[Dry run - no files written]")
        return
    
    # Write each year to a separate file
    print("\nWriting split files...")
    for year in sorted(recordsByYear.keys()):
        outputPath = outputDir / f"Records_{year}.json"
        records = recordsByYear[year]
        
        # Write in the same format as the original
        with open(outputPath, "w") as f:
            json.dump({"locations": records}, f, indent=2)
        
        sizeMB = outputPath.stat().st_size / (1024**2)
        print(f"  Written: {outputPath.name} ({len(records):,} records, {sizeMB:.1f} MB)")
    
    print("\nDone! Split files created.")
    print(f"Original file kept at: {recordsPath}")


def main():
    args = parseArgs()
    
    try:
        recordsPath = getRecordsPath(args.takeout_dir)
        splitRecords(recordsPath, dryRun=args.dry_run)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
