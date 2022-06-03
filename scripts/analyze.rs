// Copyright 2012-2015 The Rust Project Developers. See the COPYRIGHT
// file at the top-level directory of this distribution and at
// http://rust-lang.org/COPYRIGHT.
//
// Licensed under the Apache License, Version 2.0 <LICENSE-APACHE or
// http://www.apache.org/licenses/LICENSE-2.0> or the MIT license
// <LICENSE-MIT or http://opensource.org/licenses/MIT>, at your
// option. This file may not be copied, modified, or distributed
// except according to those terms.

#[repr(C)]
pub struct AnalysisResults {
    num_printable_ascii: u32,
    num_compressed: u32,
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
