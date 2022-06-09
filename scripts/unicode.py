#!/usr/bin/env python3
#
# Copyright 2011-2022 The Rust Project Developers. See the COPYRIGHT
# file at the top-level directory of this distribution and at
# http://rust-lang.org/COPYRIGHT.
#
# Licensed under the Apache License, Version 2.0 <LICENSE-APACHE or
# http://www.apache.org/licenses/LICENSE-2.0> or the MIT license
# <LICENSE-MIT or http://opensource.org/licenses/MIT>, at your
# option. This file may not be copied, modified, or distributed
# except according to those terms.

# This script uses the following Unicode tables:
# - EastAsianWidth.txt
# - ReadMe.txt
# - UnicodeData.txt
#
# Since this should not require frequent updates, we just store this
# out-of-line and check the generated module into git.

import re, os, sys, enum, math

NUM_CODEPOINTS = 0x110000
""" An upper bound for which `range(0, NUM_CODEPOINTS)` contains Unicode's codespace. """

NUM_CODEPOINT_BITS = math.ceil(math.log2(NUM_CODEPOINTS - 1))
""" The maximum number of bits required to represent a Unicode codepoint. """

Codepoint = int
BitIndex = int

def fetch_open(f: str):
    """ Opens `f` and return its corresponding file object. If `f` isn't present on disk, fetches  
        it from `http://www.unicode.org/Public/UNIDATA/`. Exits with code 1 on failure. """
    if not os.path.exists(os.path.basename(f)):
        os.system(f"curl -O http://www.unicode.org/Public/UNIDATA/{f}")
    try:
        return open(f)
    except OSError:
        sys.stderr.write(f"cannot load {f}")
        exit(1)

def load_unicode_version() -> "tuple[int, int, int]":
    """ Returns the current Unicode version by fetching and parsing `ReadMe.txt`. """
    with fetch_open("ReadMe.txt") as readme:
        pattern = "for Version (\d+)\.(\d+)\.(\d+) of the Unicode"
        return tuple(map(int, re.search(pattern, readme.read()).groups()))

class EffectiveWidth(enum.IntEnum):
    """ For our purposes, we only care about the following character widths. 
        All East Asian Width classes resolve into either `NARROW`, `WIDE`, or `AMBIGUOUS`. """
    ZERO = 0
    """ Zero columns wide. """
    NARROW = 1
    """ One column wide. """
    WIDE = 2
    """ Two columns wide. """
    AMBIGUOUS = 3
    """ Two columns wide in a CJK context. One column wide in all other contexts. """

def load_east_asian_widths() -> "list[EffectiveWidth]":
    """ Return a list of effective widths, indexed by codepoint.
        Widths are determined by fetching and parsing `EastAsianWidth.txt`.
    
        `Neutral`, `Narrow`, and `Halfwidth` characters are assigned `EffectiveWidth.NARROW`.
    
        `Wide` and `Fullwidth` characters are assigned `EffectiveWidth.WIDE`. 
    
        `Ambiguous` chracters are assigned `EffectiveWidth.AMBIGUOUS`. """
    with fetch_open("EastAsianWidth.txt") as eaw:
        # matches a width assignment for a single codepoint, i.e. "1F336;N  # ..."
        single = re.compile("^([0-9A-F]+);(\w+) +# (\w+)")
        # matches a width assignment for a range of codepoints, i.e. "3001..3003;W  # ..."
        multiple = re.compile("^([0-9A-F]+)\.\.([0-9A-F]+);(\w+) +# (\w+)")
        # map between width category code and condensed width
        width_codes = { **{c: EffectiveWidth.NARROW for c in ["N", "Na", "H"]}, \
                        **{c: EffectiveWidth.WIDE for c in ["W", "F"]}, \
                         "A": EffectiveWidth.AMBIGUOUS }

        width_map = []
        current = 0
        for line in eaw.readlines():
            raw_data = None # (low, high, width)
            if m := single.match(line):
                raw_data = (m.group(1), m.group(1), m.group(2))
            elif m := multiple.match(line):
                raw_data = (m.group(1), m.group(2), m.group(3))
            else:
                continue
            low = int(raw_data[0], 16)
            high = int(raw_data[1], 16)
            width = width_codes[raw_data[2]]

            assert current <= high
            while current <= high:
                # Some codepoints don't fall into any of the ranges in EastAsianWidth.txt.
                # All such codepoints are implicitly given Neural width (resolves to narrow)
                width_map.append(EffectiveWidth.NARROW if current < low else width)
                current += 1

        while len(width_map) < NUM_CODEPOINTS:
            # Catch any leftover codepoints and assign them implicit Neutral/narrow width.
            width_map.append(EffectiveWidth.NARROW)

        return width_map

def load_zero_widths() -> "list[bool]":
    """ Returns a list `l` where `l[c]` is true if codepoint `c` is considered a zero-width 
        character. `c` is considered a zero-width character if `c` is in general categories
         `Cc`, `Cf`, `Mn`, or `Me` (determined by fetching and parsing `UnicodeData.txt`). """
    with fetch_open("UnicodeData.txt") as categories:
        zw_map = []
        current = 0
        for line in categories.readlines():
            if len(raw_data := line.split(';')) != 15:
                continue
            [codepoint, name, cat_code] = [int(raw_data[0], 16), raw_data[1], raw_data[2]]
            zw = True if cat_code in ["Cc", "Cf", "Mn", "Me"] else False

            assert current <= codepoint
            while current <= codepoint:
                if name.endswith(", Last>") or current == codepoint:
                    # if name ends with Last, we backfill the width value to all codepoints since
                    # the previous codepoint (aka the start of the range)
                    zw_map.append(zw)
                else:
                    # unassigned characters are implicitly given Neutral width, which is nonzero
                    zw_map.append(False)
                current += 1

        while len(zw_map) < NUM_CODEPOINTS:
            # Catch any leftover codepoints. They must be unassigned (so nonzero width)
            zw_map.append(False)

        return zw_map

class Bucket:
    def __init__(self):
        self.cp_set = set()
        self.widths = []

    def append(self, codepoint: Codepoint, width: EffectiveWidth):
        self.cp_set.add(codepoint)
        self.widths.append(width)

    def try_extend(self, other: "Bucket") -> bool:
        (sw, ow) = (self.widths, other.widths)
        (less, more) = (sw, ow) if len(sw) <= len(ow) else (ow, sw)
        if less != more[:len(less)]:
            return False
        self.cp_set |= other.cp_set
        self.widths = more
        return True

    def codepoints(self) -> "list[Codepoint]":
        """ Return a sorted list of the codepoints in this bucket. """
        result = list(self.cp_set)
        result.sort()
        return result

def make_buckets(
        codepoints,
        width_map: "list[EffectiveWidth]",
        low_bit: BitIndex, 
        sentinel_bit: BitIndex
    ) -> "list[Bucket]":
    num_bits = sentinel_bit - low_bit
    assert num_bits > 0
    buckets = [Bucket() for _ in range(0, 2**num_bits)]
    mask = (1 << num_bits) - 1
    for codepoint in codepoints:
        buckets[ (codepoint >> low_bit) & mask ].append(codepoint, width_map[codepoint])
    return buckets

def index_buckets(buckets: "list[Bucket]") -> "tuple[list[Codepoint], list[Bucket]]":
    indices = []
    indexed = []
    for bucket in buckets:
        already_exists = False
        for i in range(0, len(indexed)):
            if indexed[i].try_extend(bucket):
                already_exists = True
                indices.append(i)
                break
        if not already_exists:
            indices.append(len(indexed))
            indexed.append(bucket)
    return (indices, indexed)

class OffsetType(enum.IntEnum):
    """ Represents the data type of a lookup table's offsets. """
    U2 = 2,
    """ Offsets are 2-bit unsigned integers, packed four-per-byte. """
    U4 = 4,
    """ Offsets are 4-bit unsigned integers, packed two-per-byte. """
    U8 = 8
    """ Each offset is a single byte (u8). """

def make_tables(
        table_cfgs: "list[tuple[OffsetType, BitIndex, BitIndex]]", 
        codepoints,
        width_map: "list[EffectiveWidth]"
    ):
    output = []
    cp_groups = [ range(0, NUM_CODEPOINTS) ]
    for (offset_type, low_bit, sentinel_bit) in table_cfgs:
        table_buckets = []
        for g in cp_groups:
            table_buckets.extend(make_buckets(g, width_map, low_bit, sentinel_bit))
        (indices, indexed) = index_buckets(table_buckets)
        cp_groups = [ bucket.codepoints() for bucket in indexed]
        output.append( (indices, offset_type) )
    return output

def emit_module(
        out_name: str, 
        unicode_version: "tuple[int, int, int]",
        data_nodes: "list[list[tuple[int, EffectiveWidth]]]",
        search_nodes: "list[list[tuple[int, EffectiveWidth]]]", 
        search_offsets: "list[int]"
    ):
    """ Outputs a Rust module to `out_name` with the following constants: 
        - `KEYS_PER_NODE: usize`, same as this script's constant `KEYS_PER_NODE`. 
        - `UNICODE_VERSION: (u8, u8, u8)`, corresponds to `unicode_version`. 
        - `DATA_NODES: [Node; ...]`, corresponds to `data_nodes`.
        - `SEARCH_NODES: [Node; ...]`, corresponds to `search_nodes`.
        - `SEARCH_OFFSETS: [usize; ...], corresponds to `search_offsets`. """
    if os.path.exists(out_name):
        os.remove(out_name)
    print(f"Outputting module to \"{out_name}\"")
    with open(out_name, "w") as of:
        # Write the file's preamble
        of.write("""// Copyright 2012-2022 The Rust Project Developers. See the COPYRIGHT
// file at the top-level directory of this distribution and at
// http://rust-lang.org/COPYRIGHT.
//
// Licensed under the Apache License, Version 2.0 <LICENSE-APACHE or
// http://www.apache.org/licenses/LICENSE-2.0> or the MIT license
// <LICENSE-MIT or http://opensource.org/licenses/MIT>, at your
// option. This file may not be copied, modified, or distributed
// except according to those terms.

// NOTE: The following code was generated by "scripts/unicode.py", do not edit directly

use search::Node;
""")
        # Write the Unicode version & doc comment
        of.write("""
/// The version of [Unicode](http://www.unicode.org/)
/// that this version of unicode-width is based on.
pub const UNICODE_VERSION: (u8, u8, u8) = (%s, %s, %s);
""" % unicode_version)

        # Write the keys per node
        of.write("""
/// (todo)
pub const KEYS_PER_NODE: usize = %s;
""" % KEYS_PER_NODE)

        # Write the search layer offsets
        of.write("""
/// (todo)
pub const SEARCH_OFFSETS: [usize; %s] = [""" % len(search_offsets))
        for offset in search_offsets[:-1]:
            of.write(f"{offset}, ")
        of.write(f"{search_offsets[-1]}];\n")

        # Write the search nodes
        of.write("""
/// (todo)
pub const SEARCH_NODES: [Node; %s] = [""" % len(search_nodes))
        for i, node in enumerate(search_nodes):
            if i in offsets:
                of.write('\n') # visually delimit the layers
            of.write(f"\t{node_to_string(node)},\n")
        of.write("];\n")

        # Write the data nodes
        of.write("""
/// (todo)
pub const DATA_NODES: [Node; %s] = [
""" % len(data_nodes))
        for node in data_nodes:
            of.write(f"\t{node_to_string(node)},\n")
        of.write("];\n")

if __name__ == "__main__":
    version = load_unicode_version()
    print("Generating module for Unicode %s.%s.%s" % version)
    
    eaw_map = load_east_asian_widths()
    zw_map = load_zero_widths()

    # Characters marked as zero-width in zw_map should be zero-width in the final map
    merge = lambda x : EffectiveWidth.ZERO if x[1] else x[0]
    width_map = list(map(merge, zip(eaw_map, zw_map)))

    # Override for soft hyphen
    width_map[0x00AD] = EffectiveWidth.NARROW

    # Override for Hangul Jamo medial vowels & final consonants
    for i in range(0x1160, 0x11FF + 1):
        width_map[i] = EffectiveWidth.ZERO

    TABLE_CFGS = [
        (OffsetType.U8, 13, NUM_CODEPOINT_BITS),
        (OffsetType.U8, 6, 13),
        (OffsetType.U2, 0, 6)
    ]

    tables = make_tables(TABLE_CFGS, range(0, len(width_map)), width_map)
    for table in tables:
        print(f"Table: {math.ceil(len(table[0]) * int(table[1]) / 8)} ")
    
    # emit_module("generated.rs", version, data_layer, flattened, offsets)