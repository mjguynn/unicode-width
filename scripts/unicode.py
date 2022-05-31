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
# out-of-line and check the generated.rs file into git.

from functools import reduce
import re, os, sys, enum

# Unicode codespace is 0..=0x10FFFF
NUM_CODEPOINTS = 0x110000

KEYS_PER_NODE = 16

# Size of a Rust u32 in bytes
SIZE_U32 = 4

def fetch_open(f):
    if not os.path.exists(os.path.basename(f)):
        os.system("curl -O http://www.unicode.org/Public/UNIDATA/%s"
                  % f)
    try:
        return open(f)
    except OSError:
        sys.stderr.write("cannot load %s" % f)
        exit(1)
    

def load_unicode_version() -> "tuple[int, int, int]":
    with fetch_open("ReadMe.txt") as readme:
        pattern = "for Version (\d+)\.(\d+)\.(\d+) of the Unicode"
        return tuple(map(int, re.search(pattern, readme.read()).groups()))

class CondensedWidth(enum.IntEnum):
    ZERO = 0
    NARROW = 1
    WIDE = 2
    AMBIGUOUS = 3

def load_condensed_widths() -> "list[CondensedWidth]":
    """Return a list of condensed widths, indexed by codepoint. 
    
    `Neutral`, `Narrow`, and `Halfwidth` characters are assigned 
    `CondensedWidth.NARROW`.
    
    `Wide` and `Fullwidth` characters are assigned `CondensedWidth.WIDE`. 
    
    `Ambiguous` chracters are assigned `CondensedWidth.AMBIGUOUS`. 

    Note that this *EXCLUSIVELY* assigns widths according to `EastAsianWidth.txt`.
    """
    with fetch_open("EastAsianWidth.txt") as eaw:
        re1 = re.compile("^([0-9A-F]+);(\w+) +# (\w+)")
        re2 = re.compile("^([0-9A-F]+)\.\.([0-9A-F]+);(\w+) +# (\w+)")
        # map between width category code and condensed width
        width_codes = { **{c: CondensedWidth.NARROW for c in ["N", "Na", "H"]}, \
                        **{c: CondensedWidth.WIDE for c in ["W", "F"]}, \
                         "A": CondensedWidth.AMBIGUOUS }

        width_map = []
        current = 0
        for line in eaw.readlines():
            raw_data = () # (low, high, width)
            if m := re1.match(line):
                raw_data = (m.group(1), m.group(1), m.group(2))
            elif m := re2.match(line):
                raw_data = (m.group(1), m.group(2), m.group(3))
            else:
                continue
            low = int(raw_data[0], 16)
            high = int(raw_data[1], 16)
            width = width_codes[raw_data[2]]

            assert current <= high
            while current <= high:
                # Some codepoints don't fall into any of the ranges in EastAsianWidth.txt.
                # All such codepoints are implicitly given Neural width.
                width_map.append(CondensedWidth.NARROW if current < low else width)
                current += 1

        while len(width_map) < NUM_CODEPOINTS:
            # Catch any leftover codepoints and assign them implicit narrow width.
            width_map.append(CondensedWidth.NARROW)

        return width_map

class CondensedCategory(enum.IntEnum):
    ZERO_WIDTH = 0,
    OTHER = 1,

def load_condensed_categories() -> "list[CondensedCategory]":
    """Returns a list of condensed categories, indexed by codepoint."""
    with fetch_open("UnicodeData.txt") as categories:
        cat_map = []
        current = 0
        for line in categories.readlines():
            if len(raw_data := line.split(';')) != 15:
                continue
            [codepoint, name, cat_code] = [int(raw_data[0], 16), raw_data[1], raw_data[2]]
            cat = CondensedCategory.ZERO_WIDTH if cat_code in ["Cf", "Mn", "Me"] \
                else CondensedCategory.OTHER

            assert current <= codepoint
            while current <= codepoint:
                if name.endswith(", Last>") or current == codepoint:
                    cat_map.append(cat) # the specified char, or filling in a range
                else:
                    cat_map.append(CondensedCategory.OTHER) # unassigned
                current += 1

        while len(cat_map) < NUM_CODEPOINTS:
            # Catch any leftover codepoints. They must be unassigned
            cat_map.append(CondensedCategory.OTHER)

        return cat_map

def merge_properties(props: "tuple[CondensedWidth, CondensedCategory]") -> "CondensedWidth":
    (eaw, cat) = props
    return CondensedWidth.ZERO if cat == CondensedCategory.ZERO_WIDTH else eaw

def compress_properties(props: "list[CondensedWidth]") -> "list[tuple[int, CondensedWidth]]":
    compressed_list = []
    last_width = None

    for codepoint, width in enumerate(props):
        if width != last_width:
            last_width = width
            compressed_list.append((codepoint, width))
    
    return compressed_list

def chunks(list, chunk_size, pad):
    for i in range(0, len(list), chunk_size):
        chunk = list[i:i+chunk_size]
        yield chunk + ([pad] * (chunk_size - len(chunk)))

def make_data_layer(compressed_props):
    descending = sorted(compressed_props, reverse=True)
    return list(chunks(descending, KEYS_PER_NODE, (0, CondensedWidth.ZERO)))

def make_search_layer(data_layer, height):
    keys = []
    step = (KEYS_PER_NODE+1)**height
    for chunk in chunks(data_layer, step, [(0, CondensedWidth.ZERO)] * step):
        keys.append(chunk[-1][-1])
    # remove every (KEYS_PER_NODE + 1)th node
    del keys[KEYS_PER_NODE::(KEYS_PER_NODE+1)]
    return list(chunks(keys, KEYS_PER_NODE, (0, CondensedWidth.ZERO)))

def flatten_search_layers(bottom_up):
    top_down = list(reversed(bottom_up))
    flattened = list(reduce(lambda x,y : x+y, top_down, []))
    layer_offsets = [0]
    for layer in top_down:
        layer_offsets.append(layer_offsets[-1] + len(layer))
    layer_offsets.pop() # remove the last offset, it's not needed
    return (flattened, layer_offsets)

def node_to_string(node: "list[tuple[int, CondensedWidth]]") -> str:
    out = "Node::new(["
    for (codepoint, width) in node:
        out += f"('\\u{{{codepoint:06X}}}', {width}), "
    out += "])"
    return out 

def emit_module(
    out_name: str, 
    version: "tuple[int, int, int]",
    data: "list[list[tuple[int, CondensedWidth]]]",
    flattened_search: "list[list[tuple[int, CondensedWidth]]]", 
    offsets: "list[int]"
    ):
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
""" % version)

        # Write the keys per node
        of.write("""
/// (todo)
pub const KEYS_PER_NODE: usize = %s;
""" % KEYS_PER_NODE)

        # Write the search layer offsets
        of.write("""
/// (todo)
pub const SEARCH_OFFSETS: [usize; %s] = [""" % len(offsets))
        for offset in offsets[:-1]:
            of.write(f"{offset}, ")
        of.write(f"{offsets[-1]}];\n")

        # Write the search nodes
        of.write("""
/// (todo)
pub const SEARCH_NODES: [Node; %s] = [""" % len(flattened_search))
        for i, node in enumerate(flattened_search):
            if i in offsets:
                of.write('\n') # visually delimit the layers
            of.write(f"\t{node_to_string(node)},\n")
        of.write("];\n")

        # Write the data nodes
        of.write("""
/// (todo)
pub const DATA_NODES: [Node; %s] = [
""" % len(data))
        for node in data:
            of.write(f"\t{node_to_string(node)},\n")
        of.write("];\n")

if __name__ == "__main__":
    version = load_unicode_version()
    print("Generating module for Unicode %s.%s.%s" % version)
    
    eaw_map = load_condensed_widths()
    cat_map = load_condensed_categories()
    property_map = list(map(merge_properties, zip(eaw_map, cat_map)))
    # Override for soft hyphen
    property_map[0x00AD] = CondensedWidth.NARROW
    # Override for Hangul Jamo medial vowels & final consonants
    for i in range(0x1160, 0x11FF + 1):
        property_map[i] = CondensedWidth.ZERO

    compressed_map = compress_properties(property_map)
    print(f"Compressed partition has {len(compressed_map)} keys")

    print("Building representation...")
    data_layer = make_data_layer(compressed_map)
    print(f"\t{len(data_layer)} data nodes")

    search_layers = []
    while True:
        search_layers.append(layer := make_search_layer(data_layer, len(search_layers)))
        print(f"\t{len(layer)} search node(s)")
        if len(layer) <= 1:
            break

    (flattened_search, offsets) = flatten_search_layers(search_layers)
    approx_memory = (len(flattened_search) + len(data_layer)) * KEYS_PER_NODE * SIZE_U32
    print(f"Representation size: {approx_memory} bytes")

    emit_module("generated.rs", version, data_layer, flattened_search, offsets)