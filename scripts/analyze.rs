// Copyright 2012-2022 The Rust Project Developers. See the COPYRIGHT
// file at the top-level directory of this distribution and at
// http://rust-lang.org/COPYRIGHT.
//
// Licensed under the Apache License, Version 2.0 <LICENSE-APACHE or
// http://www.apache.org/licenses/LICENSE-2.0> or the MIT license
// <LICENSE-MIT or http://opensource.org/licenses/MIT>, at your
// option. This file may not be copied, modified, or distributed
// except according to those terms.

//! Utilities for constructing and analyzing multi-level lookup tables for Unicode width.
//! CPython is too slow to perform the analyses itself; instead, `unicode.py` compiles this
//! to a dylib at runtime, then calls into it via `ctypes` FFI. 

/// An upper bound on the bits needed to represent any Unicode codepoint. More specifically,
/// all Unicode codepoints have a lower value than 2^UNICODE_BITS.
const UNICODE_BITS: u8 = 21;

#[derive(Debug)]
struct Bits {
    /// The indices of the bits the instance represents, sorted in ascending order.
    indices: Vec<u8>
}
impl Bits {
    /// Returns an instance representing `self \ sub` (set minus).
    fn without(&self, sub: &Bits) -> Self {
        let mut out_indices = Vec::with_capacity(self.indices.len());
        let mut si = 0;
        // this is basically the merge step of mergesort, but in reverse
        for &index in &self.indices {
            while si < sub.indices.len() && sub.indices[si] < index {
                si += 1;
            }
            if si >= sub.indices.len() || sub.indices[si] != index {
                out_indices.push(index)
            }
        }
        Self { indices: out_indices }
    }
    /// Returns an iterator over all combinations of `n` bits from this instance.
    fn combinations(&self, n: usize) -> BitComb<'_> {
        BitComb::new(&self, n)
    }
    /// The number of bits represented by this instance.
    fn count(&self) -> usize {
        self.indices.len()
    }
    /// Creates a mask, where bit `i` is set iff bit `i` is included in this instance.
    fn mask(&self) -> usize {
        let mut result = 0;
        for &bit_index in self.indices.iter() {
            result |= 1 << bit_index;
        } 
        result
    }
}
impl<T: IntoIterator<Item = u8>> From<T> for Bits {
    fn from(input: T) -> Self {
        let mut indices: Vec<u8> = input.into_iter().collect();
        indices.as_mut_slice().sort_unstable();
        Self { indices }
    }
}

struct BitComb<'a> {
    bits: &'a Bits,
    indices: Vec<usize>,
}
impl<'a> BitComb<'a> {
    fn new(bits: &'a Bits, n: usize) -> Self {
        let mut indices = Vec::with_capacity(n);
        let num_bits = bits.indices.len();
        // if bits has < n elements, or n==0, there are no combinations
        if num_bits >= n && n > 0 {
            (0..n).for_each(|i| indices.push(num_bits - (n - i)));
            //  corrects the first iteration so that we dont miss a combo
            indices[0] += 1;
        }
        Self { bits, indices }
    }
}
impl<'a> Iterator for BitComb<'a> {
    type Item = Bits;
    fn next(&mut self) -> Option<Self::Item> {
        for i in 0..self.indices.len() {
            let bit_index = self.indices[i];
            if bit_index > i {
                for j in 0..=i {
                    self.indices[j] = bit_index - (i - j + 1)
                }
                let selected = self.indices.iter().map(|&bi| self.bits.indices[bi]);
                return Some(Bits::from(selected))
            }
        }
        None
    }
}

fn build_lut(widths: &[u8], bits: &Bits) -> (usize, Vec<usize>) {
    // First, bucket the codepoints
    let num_buckets = 1 << bits.count();
    let bucket_size = (1 << UNICODE_BITS) / num_buckets;
    let mut buckets = vec![Vec::with_capacity(bucket_size); char::MAX as usize];
    let mask = bits.mask();
    for codepoint in 0..(char::MAX as usize) {
        buckets[codepoint & mask].push(widths[codepoint])
    }
    // Now, figure out which buckets have identical width patterns
    let mut patterns = std::collections::HashMap::new();
    let mut lut = Vec::with_capacity(num_buckets);
    for pattern in buckets.into_iter() {
        if pattern.is_empty() {
            continue;
        }
        let len = patterns.len();
        let index = *patterns.entry(pattern).or_insert(len);
        lut.push(index);
    }
    (patterns.len(), lut)
} 
fn optimal_lut(widths: &[u8], usable: Bits, index_bits: usize) -> (Bits, Vec<usize>, usize) {
    let mut opt = (Bits::from(0..1), Vec::new(), usize::MAX); // dummy value
    for candidate in usable.combinations(index_bits) {
        let (size, lut) = build_lut(widths, &candidate);
        if size < opt.2 {
            eprintln!("Lower! Size: {size}");
            opt = (candidate, lut, size)
        }
    }
    opt
}

#[no_mangle]
pub unsafe extern "C" fn optimal(widths: *const u8, widths_len: usize) {
    let widths = std::slice::from_raw_parts(widths, widths_len);
    let (bits, lut, size) = optimal_lut(widths, Bits::from(0..UNICODE_BITS), 6);
    eprintln!("Final: bits {bits:?}, size {size}");
    eprintln!("{lut:?}");
}