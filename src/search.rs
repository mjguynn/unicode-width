// Copyright 2012-2022 The Rust Project Developers. See the COPYRIGHT
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
    fn width(&self, is_cjk: bool) -> usize {
        match (self.0 & WIDTH_MASK, is_cjk) {
            (3, true) => 2,
            (3, false) => 1,
            (w, _) => w as usize
        }
    }
}
impl From<Key> for u32 {
    fn from(input: Key) -> Self {
        input.0
    }
}

#[repr(align(64))]
pub struct Node {
    keys: [Key; KEYS_PER_NODE]
}
impl Node {
    pub const fn new(key_map: [(char, usize); KEYS_PER_NODE]) -> Node {
        let mut keys = [Key::new('\0', 0); KEYS_PER_NODE];
        let mut i = 0;
        while i < KEYS_PER_NODE {
            let (codepoint, width) = key_map[i];
            keys[i] = Key::new(codepoint, width);
            i += 1;
        }
        Node { keys }
    }
    fn search(&self, needle: Needle) -> usize {
        for i in 0..KEYS_PER_NODE {
            if self.keys[i].less_than(needle) {
                return i
            }
        }
        return KEYS_PER_NODE
    }
    fn needle_width(&self, needle: Needle, is_cjk: bool) -> usize {
        for i in 0..(KEYS_PER_NODE-1) {
            if self.keys[i].less_than(needle) {
                return self.keys[i].width(is_cjk)
            }
        }
        return self.keys[KEYS_PER_NODE-1].width(is_cjk)
    }
}

pub fn lookup_width(codepoint: char, is_cjk: bool) -> usize {
    let needle = Needle::new(codepoint);
    // use the search nodes to get the offset of the data block
    let mut index = 0;
    for layer in SEARCH_OFFSETS {
        let node = &SEARCH_NODES[layer + index];
        index = (index * (KEYS_PER_NODE + 1)) + node.search(needle);
    }
    // grab that block from the data layer and linearly search through its keys
    return DATA_NODES[index].needle_width(needle, is_cjk)
}