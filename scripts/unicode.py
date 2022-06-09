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
        self.entry_set = set()
        self.widths = []

    def append(self, codepoint: Codepoint, width: EffectiveWidth):
        self.entry_set.add( (codepoint, width) )
        self.widths.append(width)

    def try_extend(self, other: "Bucket") -> bool:
        (sw, ow) = (self.widths, other.widths)
        (less, more) = (sw, ow) if len(sw) <= len(ow) else (ow, sw)
        if less != more[:len(less)]:
            return False
        self.entry_set |= other.entry_set
        self.widths = more
        return True

    def entries(self) -> "list[tuple[Codepoint, EffectiveWidth]]":
        """ Return a sorted list of the codepoint/width pairs in this bucket. """
        result = list(self.entry_set)
        result.sort()
        return result

def make_buckets(entries, low_bit: BitIndex, num_bits: BitIndex) -> "list[Bucket]":
    assert num_bits > 0
    buckets = [Bucket() for _ in range(0, 2**num_bits)]
    mask = (1 << num_bits) - 1
    for (codepoint, width) in entries:
        buckets[ (codepoint >> low_bit) & mask ].append(codepoint, width)
    return buckets

def index_buckets(buckets) -> "tuple[list[Codepoint], list[Bucket]]":
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

class OffsetType(enum.Enum):
    """ Represents the data type of a lookup table's offsets. The integer value assigned to each
        variant represents the number of TODO"""
    U2 = (2, 2)
    """ Offsets are 2-bit unsigned integers, packed four-per-byte. """
    U4 = (4, 1)
    """ Offsets are 4-bit unsigned integers, packed two-per-byte. """
    U8 = (8, 0)
    """ Each offset is a single byte (u8). """

class Table:
    def __init__(self, entry_groups, low_bit: BitIndex, cap_bit: BitIndex, offset_type: OffsetType):
        self.low_bit = low_bit
        self.num_bits = cap_bit - low_bit
        self.offset_type = offset_type
        self.indices = []
        self.indexed = []

        buckets = []
        for entries in entry_groups:
            buckets.extend(make_buckets(entries, self.low_bit, self.num_bits))

        # Index buckets
        for bucket in buckets:
            for (i, existing) in enumerate(self.indexed):
                if existing.try_extend(bucket):
                    self.indices.append(i)
                    break
            else:
                self.indices.append(len(self.indexed))
                self.indexed.append(bucket)

        # Validate offset type -- then set it upon success
        for index in self.indices:
            assert index < (1 << self.offset_type.value[0])

    def buckets(self):
        return self.indexed
        
    def indexing_info(self):
        byte_bits = self.offset_type.value[1]
        byte_info = None
        if byte_bits != 0:
            emask = (1 << self.offset_type.value[1]) - 1
            byte_info = (self.low_bit, (1 << byte_bits) - 1, self.offset_type.value[0], emask)
        return (self.low_bit + byte_bits, (1 << (self.num_bits - byte_bits)) - 1, byte_info)

    def byte_array(self) -> "list[int]":
        indices_per_byte = 8 // self.offset_type.value[0]
        byte_array = []
        for i in range(0,len(self.indices),indices_per_byte):
            byte = 0
            for j in range(0,indices_per_byte):
                byte |= self.indices[i+j] << (j*self.offset_type.value[0])
            byte_array.append(byte)
        return byte_array

        
def make_tables(table_cfgs: "list[tuple[BitIndex, BitIndex, OffsetType]]", entries) -> "list[Table]":
    output = []
    entry_groups = [ entries ]
    for (low_bit, cap_bit, offset_type) in table_cfgs:
        table = Table(entry_groups, low_bit, cap_bit, offset_type)
        entry_groups = map(lambda bucket: bucket.entries(), table.buckets())
        output.append(table)
    return output

def emit_module(out_name: str, unicode_version: "tuple[int, int, int]", tables):
    """ Outputs a Rust module to `out_name` with the following constants: 
        - `UNICODE_VERSION: (u8, u8, u8)`, corresponds to `unicode_version`. 
        - `TABLE_{i}: [u8; ...]`, corresponds to `tables[i]` for all valid `i` """
    if os.path.exists(out_name):
        os.remove(out_name)
    with open(out_name, "w") as of:
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
""")
        of.write("""
/// The version of [Unicode](http://www.unicode.org/)
/// that this version of unicode-width is based on.
pub const UNICODE_VERSION: (u8, u8, u8) = (%s, %s, %s);
""" % unicode_version)

        of.write("""
pub mod charwidth {
    use core::option::Option::{self, None, Some};

    #[inline]
    fn lookup_width(c: char, is_cjk: bool) -> usize {
        let cp = c as usize;

""")

        mul_var = 1
        index_var = 0
        for (i, table) in enumerate(tables):
            (lrs, mask, byte_indexing_info) = table.indexing_info()
            table_index = f"{mul_var} * ({index_var} as usize) + ( cp >> {lrs} & 0x{mask:0X} )"
            byte_index = ""
            if byte_indexing_info:
                (blrs, bmask, bmul, emask) = byte_indexing_info
                byte_index = f" >> ({bmul} * (cp >> {blrs} & 0x{bmask:0X})) & 0x{emask:0X}"
            of.write(f"\t\tlet i{i} = TABLE_{i}[{table_index}]{byte_index};\n")
            mul_var = len(table.buckets())
            index_var = f"i{i}"

        of.write("""
        if %s == 3 {
            if is_cjk { 
                2 
            } else { 
                1 
            }
        } else {
            %s.into()
        }
    }
    """ % (index_var, index_var))

        of.write("""
    #[inline(always)]
    pub fn width(c: char, is_cjk: bool) -> Option<usize> {
        if c < '\\u{7F}' {
            if c >= '\\u{20}' {
                // U+0020 to U+007F (exclusive) are single-width ASCII
                Some(1)
            } else if c == '\\0' {
                // U+0000 *is* a control code, but it's special-cased
                Some(0)
            } else {
                // U+0001 to U+0020 (exclusive) are control codes
                None
            }
        } else if c >= '\\u{A0}' {
            Some(lookup_width(c, is_cjk))
        } else {
            // U+007F to U+00A0 (exclusive) are control codes
            None
        }
    }
    """)

        for (i, table) in enumerate(tables):
            byte_array = table.byte_array()
            of.write("""
    /// (todo)
    const TABLE_%s: [u8; %s] = [""" % (i, len(byte_array)))
            for (j, byte) in enumerate(byte_array):
                if j % 12 == 0:
                    of.write("\n\t\t")
                of.write(f"0x{byte:02X}, ")
            of.write("\n\t];\n")
        of.write("""
}
""")
        
            
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

    MAX_CODEPOINT_BITS = math.ceil(math.log2(NUM_CODEPOINTS - 1))
    TABLE_CFGS = [
        (13, MAX_CODEPOINT_BITS, OffsetType.U8),
        (6, 13, OffsetType.U8),
        (0, 6, OffsetType.U2)
    ]
    tables = make_tables(TABLE_CFGS, enumerate(width_map))

    print("------------------------")
    total_size = 0
    for (i, table) in enumerate(tables):
        size_bytes = len(table.byte_array())
        print(f"Table {i} Size: {size_bytes} bytes")
        total_size += size_bytes
    print("------------------------")
    print(f"  Total Size: {total_size} bytes")

    OUT_NAME = "tables.rs"
    emit_module(OUT_NAME, version, tables)
