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
    /// Copies selected bits from `target` into the leftmost contiguous bits of the output, with
    /// all other bits set to zero.
    fn extract(&self, target: usize) -> usize {
        let mut result = 0;
        for (i, &bit_index) in self.indices.iter().enumerate() {
            result |= ((target >> bit_index) & 1) << i;
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
    remaining: usize
}
impl<'a> BitComb<'a> {
    fn new(bits: &'a Bits, n: usize) -> Self {
        let mut indices = Vec::with_capacity(n);
        let num_bits = bits.count();
        // if bits has < n elements, or n==0, there are no combinations
        if num_bits >= n && n > 0 {
            (0..n).for_each(|i| indices.push(num_bits - (n - i)));
            //  corrects the first iteration so that we dont miss a combo
            indices[0] += 1;
        }
        // calculates bits.count() choose n
        let mut bits_f_div_n_f= 1;
        for i in (n+1)..=bits.count() {
            bits_f_div_n_f *= i;
        }
        let mut bits_minus_n_f = 1;
        for i in 1..=(bits.count() - n) {
            bits_minus_n_f *= i;
        }
        let remaining = bits_f_div_n_f / bits_minus_n_f;
        Self { bits, indices, remaining }
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
                self.remaining -= 1;
                return Some(Bits::from(selected))
            }
        }
        None
    }
    fn size_hint(&self) -> (usize, Option<usize>) {
        (self.remaining, Some(self.remaining))
    }
}
impl<'a> ExactSizeIterator for BitComb<'a> {}

#[derive(Clone)]
struct LookupBucket {
    chars: Vec<char>,
    widths: Vec<u8>,
}
impl LookupBucket {
    fn with_capacity(cap: usize) -> Self {
        LookupBucket { chars: Vec::with_capacity(cap), widths: Vec::with_capacity(cap) }
    }
    fn push(&mut self, ch: char, width: u8) {
        if let Some(&l) = self.chars.last() {
            assert!( l < ch )
        }
        self.chars.push(ch);
        self.widths.push(width);
    }
    fn consume(&mut self, rhs: &mut LookupBucket) -> bool {
        if self.widths.len() < rhs.widths.len() {
            if self.widths.as_slice() == &rhs.widths[0..self.widths.len()] {
                std::mem::swap(&mut self.widths, &mut rhs.widths);
                self.chars.append(&mut rhs.chars);
                self.chars.sort();
                return true;
            }
        }
        else {
            if &self.widths[0..rhs.widths.len()] == rhs.widths.as_slice() {
                self.chars.append(&mut rhs.chars);
                self.chars.sort();
                return true;
            }
        }
        false
    }
}
struct LookupTable {
    /// Indices into buckets
    table: Vec<usize>,
    buckets: Vec<LookupBucket>
}
impl LookupTable {
    fn new(widths: &[u8], chars: &[char], bits: &Bits) -> Self {
        let num_buckets = 1 << bits.count();
        // overestimation
        let bucket_size = (1 << UNICODE_BITS) / num_buckets;
        let mut raw_buckets = vec![LookupBucket::with_capacity(bucket_size); num_buckets];
        for &ch in chars {
            raw_buckets[bits.extract(ch as usize)].push(ch, widths[ch as usize])
        }
        let mut table = Vec::new();
        let mut buckets: Vec<LookupBucket> = Vec::new();
        'next_raw_bucket: for mut raw_bucket in raw_buckets.into_iter() {
            for i in 0..buckets.len() {
                if buckets[i].consume(&mut raw_bucket) {
                    table.push(i);
                    continue 'next_raw_bucket;
                }
            }
            table.push(buckets.len());
            buckets.push(raw_bucket);
        }
        Self { table, buckets }
    }
    fn bucket_count(&self) -> usize {
        self.buckets.len()
    }
}
fn optimal_lut(widths: &[u8], chars: &[char], usable: Bits, index_bits: usize) 
    -> (Bits, LookupTable) 
{
    use std::io::Write;
    eprintln!(""); // move cursor down one line

    let mut combinations = usable.combinations(index_bits);
    let num_combinations = combinations.len();

    let mut min = (usize::MAX, None); // dummy value
    for (i, combo) in combinations.enumerate() {
        let table = LookupTable::new(widths, chars, &combo);
        let key = table.bucket_count();
        if key < min.0 {
            min = (key, Some((combo, table)))
        }
        if i % 64 == 0 {
            let pcnt = (i as f64) / (num_combinations as f64) * 100.0f64;
            eprint!("\r\tProgress: {i}/{num_combinations} ({pcnt:.2}%)");
            std::io::stderr().flush();
        }
    }

    eprint!("\x1BM"); // move cursor up one line
    return min.1.unwrap();
}

#[no_mangle]
pub unsafe extern "C" fn optimal(widths: *const u8, widths_len: usize) {
    let widths = std::slice::from_raw_parts(widths, widths_len);
    let chars: Vec<_> = (0..u32::from(char::MAX)).filter_map(|c| char::try_from(c).ok()).collect();
    let (bits, table) = optimal_lut(widths, &chars, Bits::from(0..UNICODE_BITS), 6);
    eprintln!("OPTIMAL: {bits:?}, size {}", table.bucket_count());
}