// Copyright 2012-2015 The Rust Project Developers. See the COPYRIGHT
// file at the top-level directory of this distribution and at
// http://rust-lang.org/COPYRIGHT.
//
// Licensed under the Apache License, Version 2.0 <LICENSE-APACHE or
// http://www.apache.org/licenses/LICENSE-2.0> or the MIT license
// <LICENSE-MIT or http://opensource.org/licenses/MIT>, at your
// option. This file may not be copied, modified, or distributed
// except according to those terms.

use crate::generated::{KEYS_PER_NODE, SEARCH_OFFSETS, SEARCH_NODES, DATA_NODES};

const CODEPOINT_SHIFT: u32 = 4;

const WIDTH_MASK: u32 = 0b11;

#[derive(Clone,Copy)]
struct Needle(u32);
impl Needle {
    fn new(codepoint: char) -> Self {
        Self((u32::from(codepoint) << CODEPOINT_SHIFT) | (WIDTH_MASK + 1))
    }
}
impl From<Needle> for u32 {
    fn from(input: Needle) -> Self {
        input.0
    }
}

#[derive(Clone,Copy)]
struct Key(u32);
impl Key {
    const fn new(codepoint: char, width: usize) -> Self {
        assert!(width <= WIDTH_MASK as usize);
        Self(((codepoint as u32) << CODEPOINT_SHIFT) | (width as u32))
    }
    fn less_than(&self, needle: Needle) -> bool {
        self.0 < u32::from(needle)
    }
    fn width(&self) -> usize {
        (self.0 & WIDTH_MASK) as usize
    }
}
impl From<Key> for u32 {
    fn from(input: Key) -> Self {
        input.0
    }
}

/// A group of [KEYS_PER_NODE] keys, stored as `u32`s in descending order.
/// # Alignment
/// Currently, each `Node` is 64 bytes, which is the typical size of a cache line.
/// With 64 byte alignment each `Node` will fit into exactly one cache line.
/// If the size of this struct ever changes, its alignment may need to be modified.
#[repr(align(64))]
pub struct Node {
    keys: [Key; KEYS_PER_NODE]
}
impl Node {
    pub const fn from_keys(keys: [(char, usize); KEYS_PER_NODE]) -> Node {
        let mut compressed_keys = [Key::new('\0', 0); KEYS_PER_NODE];
        let mut i = 0;
        while i < KEYS_PER_NODE {
            let (codepoint, width) = keys[i];
            compressed_keys[i] = Key::new(codepoint, width);
            i += 1;
        }
        Node { keys: compressed_keys }
    }
    fn search(&self, needle: Needle) -> usize {
        for i in 0..KEYS_PER_NODE {
            if self.keys[i].less_than(needle) {
                return i
            }
        }
        return KEYS_PER_NODE
    }
    fn width(&self, needle: Needle) -> usize {
        for i in 0..(KEYS_PER_NODE-1) {
            if self.keys[i].less_than(needle) {
                return self.keys[i].width()
            }
        }
        return self.keys[KEYS_PER_NODE-1].width()
    }
}

pub fn table_width(codepoint: char) -> usize {
    let needle = Needle::new(codepoint);
    // use the search nodes to get the offset of the data block
    let mut index = 0;
    for layer in SEARCH_OFFSETS {
        let node = &SEARCH_NODES[layer + index];
        index = (index * (KEYS_PER_NODE + 1)) + node.search(needle);
    }
    // grab that block from the data layer and linearly search through its keys
    return DATA_NODES[index].width(needle)
}