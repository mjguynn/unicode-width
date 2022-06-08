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
    /// Returns the number of discontinuities in the selected bits.
    fn discontinuities(&self) -> usize {
        let mut discontinuities = 0;
        for i in 1..self.indices.len() {
            if self.indices[i] > self.indices[i-1] + 1 {
                discontinuities += 1;
            }
        }
        discontinuities
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
struct Bucket {
    chars: Vec<char>,
    widths: Vec<u8>,
}
impl Bucket {
    fn with_capacity(cap: usize) -> Self {
        Self { chars: Vec::with_capacity(cap), widths: Vec::with_capacity(cap) }
    }
    fn from_chars(widths: &[u8], chars: impl Iterator<Item = char>) -> Self {
        let mut bucket = Self::with_capacity(chars.size_hint().0);
        for ch in chars {
            bucket.push(ch, widths[ch as usize])
        }
        bucket
    }
    fn push(&mut self, ch: char, width: u8) {
        if let Some(&l) = self.chars.last() {
            assert!( l < ch )
        }
        self.chars.push(ch);
        self.widths.push(width);
    }
    fn consume(&mut self, rhs: &mut Self) -> bool {
        if self.widths.len() < rhs.widths.len() {
            if self.widths.as_slice() == &rhs.widths[0..self.widths.len()] {
                std::mem::swap(&mut self.widths, &mut rhs.widths);
                self.chars.append(&mut rhs.chars);
                self.chars.sort();
                return true;
            }
        }
        else if &self.widths[0..rhs.widths.len()] == rhs.widths.as_slice() {
            self.chars.append(&mut rhs.chars);
            self.chars.sort();
            return true;
        }
        false
    }
    fn iter(&self) -> impl '_ + Iterator<Item = (&char, &u8)> {
        self.chars.iter().zip(self.widths.iter())
    }
    fn size(&self) -> usize { self.chars.len() }
}

fn make_buckets(parent: &Bucket, bits: &Bits) -> Vec<Bucket> {
    let num_buckets = 1 << bits.count();
    let bucket_size = 2*parent.size() / num_buckets;
    let mut buckets = vec![Bucket::with_capacity(bucket_size); num_buckets];
    for (&c, &width) in parent.iter() {
        buckets[bits.extract(c as usize)].push(c, width)
    }
    buckets
}

struct IndexedBuckets {
    indexes: Vec<usize>,
    buckets: Vec<Bucket>
}
impl IndexedBuckets {
    fn indexes(&self) -> &[usize] {
        self.indexes.as_slice()
    }
    fn buckets(&self) -> &[Bucket] {
        self.buckets.as_slice()
    }
    fn is_uniform(&self) -> bool {
        for bucket in self.buckets.iter() {
            let mut iter = bucket.iter();
            if let Some((_, width)) = iter.next() {
                if !iter.all(|(_, w)| width == w) {
                    return false;
                }
            }
        }
        true
    }
}
impl<T: IntoIterator<Item = Bucket>> From<T> for IndexedBuckets {
    fn from(input: T) -> Self {
        let mut indexes = Vec::new();
        let mut buckets: Vec<Bucket> = Vec::new();
        'next_bucket: for mut bucket in input.into_iter() {
            // the linear search has an unfortunate time complexity, and a hashmap would be
            // preferable... however there's some tricky subset logic in consume that can't
            // be directly translated to the hashmap
            for i in 0..buckets.len() {
                if buckets[i].consume(&mut bucket) {
                    indexes.push(i);
                    continue 'next_bucket;
                }
            }
            indexes.push(buckets.len());
            buckets.push(bucket);
        }
        Self { indexes, buckets }
    }
}

fn optimal_table(
    parent_buckets: &[Bucket], 
    usable: Bits, 
    index_bits: &[usize], 
    max_discontinuities: usize 
) 
    -> Option<Vec<(Bits, IndexedBuckets)>>
{
    use std::io::Write;

    if index_bits.is_empty() {
        return None;
    }
    
    let combinations = usable.combinations(index_bits[0]);
    let num_combinations = combinations.len();
    eprint!("\n\tProgress: 0/{num_combinations} (0.00%)");

    let mut min = (usize::MAX, None); // dummy value
    for (i, combo) in combinations.enumerate() {
        if combo.discontinuities() > max_discontinuities {
            continue;
        }
        let mut combo_buckets = Vec::new();
        for pb in parent_buckets {
            let mut buckets = make_buckets(pb, &combo);
            combo_buckets.append(&mut buckets);
        }
        let indexed = IndexedBuckets::from(combo_buckets);
        if indexed.is_uniform() {
            min = (0, Some(vec![(combo, indexed)]));
            break;
        }
        if let Some(mut recurse) = optimal_table(
            indexed.buckets(), 
            usable.without(&combo), 
            &index_bits[1..],
            max_discontinuities
        ) {
            recurse.push((combo, indexed));
            let unique_buckets = recurse.iter().map(|(_, ib)| ib.buckets().len()).sum();
            if unique_buckets < min.0 {
                min = (unique_buckets, Some(recurse));
            }
        }
        if i % 8 == 0 {
            let pcnt = (i as f64) / (num_combinations as f64) * 100.0f64;
            eprint!("\r\tProgress: {i}/{num_combinations} ({pcnt:.2}%) \t(min: {})", min.0);
            std::io::stderr().flush().unwrap();
        }
    }

    eprint!("\x1B[1A"); // move cursor up one line
    min.1
}

#[no_mangle]
pub unsafe extern "C" fn optimal(widths: *const u8, widths_len: usize) {
    let widths = std::slice::from_raw_parts(widths, widths_len);
    let chars = Bucket::from_chars(
        widths,
        (0..u32::from(char::MAX)).filter_map(|c| char::try_from(c).ok())
    );
    let opt = optimal_table(&[chars], Bits::from(0..UNICODE_BITS), &[6,7,8], 1).unwrap();
    eprintln!("Constructed {}-level lookup table:", opt.len());
    for (bits, table) in opt.iter().rev() {
        eprintln!("\tBits: {bits:?}, buckets: {}", table.buckets().len());
    }
}