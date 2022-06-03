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

from functools import reduce
import re, os, sys, enum


NUM_CODEPOINTS = 0x110000
""" An upper bound for which `range(0, NUM_CODEPOINTS)` contains the entire Unicode codespace. """

KEYS_PER_NODE = 12
""" The number of keys in each node. 
    Note that each node has (KEYS_PER_NODE + 1) children, except for data (leaf) nodes. """

NODE_ALIGNMENT = 64
""" The alignment of each node, in bytes.
    Ideally, this should be kept in sync with `#[repr(align(N))]` in `search.rs`, but it only 
    affects the size estimation this script prints to the console. """

KEY_SIZE = 4
""" The size of each key, in bytes.
    Ideally, this should be kept in sync with `search.rs`, but like `NODE_ALIGNMENT` it only
    affects size estimation."""

HASH = [(9, 2), (16,4)]
""" todo: (start, shift) hash segment lengths"""

def fetch_open(f):
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
    """ For our purposes, we only care about the following character widths. All East Asian Width
        classes resolve into either `NARROW`, `WIDE`, or `AMBIGUOUS`. """
    ZERO = 0
    """ Zero columns wide. """
    NARROW = 1
    """ One column wide. """
    WIDE = 2
    """ Two columns wide. """
    AMBIGUOUS = 3
    """ Two columns wide in a CJK context. One column wide in all other contexts. """
    DEFER = 4
    """ This isn't a real width. It can only occur in the context of the hash map, and indicates 
        that the search/data nodes should be consulted for the actual width of the character. """

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
            raw_data = () # (low, high, width)
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
    """ Returns a list `l` where `l[c]` is true iff codepoint `c` is considered a zero-width 
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
                    zw_map.append(zw) # the specified char, or filling in a range
                else:
                    zw_map.append(False) # unassigned characters have non-zero-width
                current += 1

        while len(zw_map) < NUM_CODEPOINTS:
            # Catch any leftover codepoints. They must be unassigned
            zw_map.append(False)

        return zw_map
        
def hash(codepoint: int) -> int:
    hashed = 0
    next_index = 0
    for (start, len) in HASH:
        hashed |= ((codepoint >> start) & ((1 << len) - 1)) << next_index
        next_index += len
    return hashed

def hash_to_rust_expr(param_name: str) -> str:
    """ Generates a Rust expression which takes a variable named `param_name` and evaluates 
        to the hashed version of `param_name` as specified by CODEPOINT_HASH. """
    extract = lambda start, len : f"({param_name} >> {start}) & 0b{(1 << len) - 1:b}"
    start, len_acc = HASH[0]
    expr = f"({extract(start, len_acc)})"
    for (start, len) in HASH[1:]:
        expr += f" | (({extract(start, len)}) << {len_acc})"
        len_acc += len
    return expr 

def build_hash_table(width_map: "list[EffectiveWidth]") -> "list[EffectiveWidth]":
    # total number of bits in the hash table is equal to the sum of the hash segment lengths
    HASH_BITS = reduce(lambda acc, r: acc + r[1], HASH, 0)
    table = [None] * 2**HASH_BITS
    for codepoint, width in enumerate(width_map):
        hashed = hash(codepoint)
        cur = table[hashed]
        if cur and cur != width:
            table[hashed] = EffectiveWidth.DEFER
        else:
            table[hashed] = width
    return table

def compress_widths(widths) -> "list[tuple[int, EffectiveWidth]]":
    """ Input: an array for which `widths[c]` is the computed width of codepoint `c`.
        Output: `compressed`, a partition of the codespace into ranges of uniform effective width.
        Each element in `compressed` is a codepoint-width tuple; the elements are sorted in 
        increasing order of codepoint, and the width of element `i` applies to all codepoints from
        `compressed[i][0]` to `compressed[i+1][0]` (exclusive), or to all codepoints greater than
        `compressed[i][0]` if `compressed[i+1]` does not exist. """
    assert len(widths) == NUM_CODEPOINTS
    compressed = []
    last_width = None
    for codepoint, width in enumerate(widths):
        if width != last_width:
            last_width = width
            compressed.append((codepoint, width))
    
    return compressed

def chunks(list, chunk_size, pad):
    """ Returns an iterator over `list` which yields `chunk_size` slices of `list`, padding with 
        `pad` if necessary. """
    for i in range(0, len(list), chunk_size):
        chunk = list[i:i+chunk_size]
        yield chunk + ([pad] * (chunk_size - len(chunk)))

def make_data_layer(compressed_widths: "list[tuple[int, EffectiveWidth]]"):
    """ Converts `compressed_widths` to a list of nodes (each is a list of `KEYS_PER_NODE` tuples), 
         sorted in descended order and padded as needed with `(0, EffectiveWidth.ZERO)`. """
    descending = sorted(compressed_widths, reverse=True)
    return list(chunks(descending, KEYS_PER_NODE, (0, EffectiveWidth.ZERO)))

def make_search_layer(data_layer: "list[list[tuple[int, EffectiveWidth]]]", height: int):
    """ Outputs a list `l` where `l[i][j]` is the minimum value of `data_layer[S*i + j]`, where 
        `S = (KEYS_PER_NODE+1)**height`. If the last node in `l` is unfilled it is padded with 
        `(0, EffectiveWidth.ZERO)`. Assumes that `data_layer` is sorted in descending order. """
    keys = []
    step = (KEYS_PER_NODE+1)**height
    for chunk in chunks(data_layer, step, [(0, EffectiveWidth.ZERO)] * step):
        keys.append(chunk[-1][-1])
    # remove every (KEYS_PER_NODE + 1)th node
    del keys[KEYS_PER_NODE::(KEYS_PER_NODE+1)]
    return list(chunks(keys, KEYS_PER_NODE, (0, EffectiveWidth.ZERO)))

def flatten_search_layers(search_layers: "list[list[tuple[int, EffectiveWidth]]]"):
    """ Merges each element/layer in `search_layers` into a consecutive array `flattened`, sorted
        in increasing order of layer size. Returns `(flattened, offsets)`, where `offsets[i]` is 
        the start offset of layer `i` (zero-indexed). """
    top_down = list(sorted(search_layers, key=lambda x : len(x)))
    flattened = list(reduce(lambda x,y : x+y, top_down, []))
    offsets = [0]
    for layer in top_down:
        offsets.append(offsets[-1] + len(layer))
    offsets.pop() # remove the last offset, it's not needed
    return (flattened, offsets)

def node_to_rust_expr(node: "list[tuple[int, EffectiveWidth]]") -> str:
    """ Outputs a string containing a Rust expression which constructs a `Node` from `node`. """
    assert len(node) == KEYS_PER_NODE
    out = "Node::new(["
    for (codepoint, width) in node:
        out += f"('\\u{{{codepoint:06X}}}', {int(width)}), "
    out += "])"
    return out 

def emit_module(
        out_name: str, 
        unicode_version: "tuple[int, int, int]",
        hash_table: "list[EffectiveWidth]",
        search_offsets: "list[int]",
        search_nodes: "list[list[tuple[int, EffectiveWidth]]]", 
        data_nodes: "list[list[tuple[int, EffectiveWidth]]]"
    ):
    """ Outputs a Rust module to `out_name` with the following constants: 
        - `UNICODE_VERSION: (u8, u8, u8)`, corresponds to `unicode_version`. 
        - `KEYS_PER_NODE: usize`, same as this script's constant `KEYS_PER_NODE`. 
        - `HASH_TABLE: [u8; ...]`, corresponds to `hash_table`.
        - `SEARCH_OFFSETS: [usize; ...], corresponds to `search_offsets`. 
        - `SEARCH_NODES: [Node; ...]`, corresponds to `search_nodes`.
        - `DATA_NODES: [Node; ...]`, corresponds to `data_nodes`. """
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
        of.write("""
/// The version of [Unicode](http://www.unicode.org/)
/// that this version of unicode-width is based on.
pub const UNICODE_VERSION: (u8, u8, u8) = (%s, %s, %s);
""" % unicode_version)

        of.write("""
/// (todo) 
pub fn hash_char(c: char) -> usize {
    let codepoint = u32::from(c);
    (%s) as usize
}
""" % hash_to_rust_expr("codepoint"))

        of.write("""
/// (todo)
pub const HASH_TABLE: [u8; %s] = [""" % len(hash_table))
        for i, width in enumerate(hash_table):
            if i % 8 == 0:
                of.write("\n\t")
            of.write(f"{width}, ")
        of.write("\n];\n")

        of.write("""
/// (todo)
pub const KEYS_PER_NODE: usize = %s;
""" % KEYS_PER_NODE)

        of.write("""
/// (todo)
pub const SEARCH_OFFSETS: [usize; %s] = [""" % len(search_offsets))
        for offset in search_offsets[:-1]:
            of.write(f"{offset}, ")
        of.write(f"{search_offsets[-1]}];\n")

        of.write("""
/// (todo)
pub const SEARCH_NODES: [Node; %s] = [""" % len(search_nodes))
        for i, node in enumerate(search_nodes):
            if i in offsets:
                of.write('\n') # visually delimit the layers
            of.write(f"\t{node_to_rust_expr(node)},\n")
        of.write("];\n")

        of.write("""
/// (todo)
pub const DATA_NODES: [Node; %s] = [
""" % len(data_nodes))
        for node in data_nodes:
            of.write(f"\t{node_to_rust_expr(node)},\n")
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

    print(f"Building hash table...")
    hash_table = build_hash_table(width_map)
    print(f"\t{len(hash_table)} bytes")

    compressed_widths = compress_widths(width_map)
    print(f"Compressed partition has {len(compressed_widths)} keys")

    print("Building representation...")
    data_layer = make_data_layer(compressed_widths)
    print(f"\t{len(data_layer)} data nodes")

    search_layers = []
    while True:
        search_layers.append(layer := make_search_layer(data_layer, len(search_layers)))
        print(f"\t{len(layer)} search node(s)")
        if len(layer) <= 1:
            break

    (flattened, offsets) = flatten_search_layers(search_layers)
    node_size = int((KEYS_PER_NODE * KEY_SIZE + NODE_ALIGNMENT - 1) / NODE_ALIGNMENT) * NODE_ALIGNMENT
    approx_memory = (len(flattened) + len(data_layer)) * node_size
    print(f"Representation size: {approx_memory} bytes")

    emit_module("generated.rs", version, hash_table, offsets, flattened, data_layer)