// Copyright 2012-2015 The Rust Project Developers. See the COPYRIGHT
// file at the top-level directory of this distribution and at
// http://rust-lang.org/COPYRIGHT.
//
// Licensed under the Apache License, Version 2.0 <LICENSE-APACHE or
// http://www.apache.org/licenses/LICENSE-2.0> or the MIT license
// <LICENSE-MIT or http://opensource.org/licenses/MIT>, at your
// option. This file may not be copied, modified, or distributed
// except according to those terms.

use crate::generated::{LAYER_OFFSETS, TREE_NODES};

const KEYS_PER_NODE: usize = 16;

const CODEPOINT_SHIFT: u32 = 4;

const WIDTH_MASK: u32 = 0b11;

#[derive(Clone,Copy)]
struct SearchNeedle(u32);
impl SearchNeedle {
    fn new(codepoint: char) -> SearchNeedle {
        SearchNeedle((u32::from(codepoint) << CODEPOINT_SHIFT) | (WIDTH_MASK + 1))
    }
}
impl From<SearchNeedle> for u32 {
    fn from(input: SearchNeedle) -> Self {
        input.0
    }
}
/// A group of [KEYS_PER_NODE] keys, stored as `u32`s in descending order.
/// # Alignment
/// Currently, each `SearchNode` is 64 bytes, which is the typical size of a cache line.
/// With 64 byte alignment each `SearchNode` will fit into exactly one cache line.
/// If the size of this struct ever changes, its alignment may need to be modified.
#[repr(align(64))]
pub struct SearchNode {
    keys: [u32; KEYS_PER_NODE]
}
impl SearchNode {
    pub const fn from_keys(uncompressed_keys: [(char, usize); KEYS_PER_NODE]) -> SearchNode {
        let mut keys = [0; KEYS_PER_NODE];
        let mut i = 0;
        while i < KEYS_PER_NODE {
            let (codepoint, width) = uncompressed_keys[i];
            assert!(width as u32 <= WIDTH_MASK);
            keys[i] = ((codepoint as u32) << CODEPOINT_SHIFT) | (width as u32);
            i += 1;
        }
        SearchNode { keys }
    }
    fn search(&self, needle: SearchNeedle) -> usize {
        for i in 0..KEYS_PER_NODE {
            if self.keys[i] < u32::from(needle) {
                return i
            }
        }
        return KEYS_PER_NODE
    }
    fn width_at(&self, index: usize) -> usize {
        (self.keys[index] & WIDTH_MASK) as usize
    }
}

pub fn table_width(c: char) -> usize {
    const NUM_LAYERS: usize = LAYER_OFFSETS.len();
    let needle = SearchNeedle::new(c);
    // search through the min-key layers
    let mut index = 0;
    for i in 0..(NUM_LAYERS-1) {
        let node = &TREE_NODES[LAYER_OFFSETS[i] + index];
        index = (index * (KEYS_PER_NODE + 1)) + node.search(needle);
    }
    // final layer
    let node = &TREE_NODES[LAYER_OFFSETS[NUM_LAYERS - 1] + index];
    return node.width_at(node.search(needle));
}