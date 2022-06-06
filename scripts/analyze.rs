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

#[repr(C)]
pub struct AnalysisResults {
    num_printable_ascii: u32,
    num_compressed: u32,
}

pub fn test(){
    let bits = Bits::from(0..UNICODE_BITS);
    for comb in bits.combinations(6) {
        println!("{comb:?}");
    }
}
/// (todo)
/// # Safety
/// The operation `std::slice::from_raw_parts(widths, widths_len)` must be sound.
#[no_mangle]
pub unsafe extern "C" fn analyze(widths: *const u8, widths_len: u32, mask: u32) -> AnalysisResults
{
    let mask = usize::try_from(mask).unwrap();
    let widths = std::slice::from_raw_parts(widths, usize::try_from(widths_len).unwrap());
    let mut table = vec![0u8; widths.len()];
    for (codepoint, &width) in widths.iter().enumerate() {
        table[codepoint & mask] |= 1 << width
    }

    let mut num_printable_ascii = 0;
    let mut num_compressed = 0;

    for i in 0..widths.len() {
        let val = table[i & mask];
        if val.is_power_of_two() {
            num_compressed += 1;
            if (0x20..0x7F).contains(&i) {
                num_printable_ascii += 1
            }
        }
    }
    AnalysisResults { num_printable_ascii, num_compressed }
}
