// Copyright 2012-2015 The Rust Project Developers. See the COPYRIGHT
// file at the top-level directory of this distribution and at
// http://rust-lang.org/COPYRIGHT.
//
// Licensed under the Apache License, Version 2.0 <LICENSE-APACHE or
// http://www.apache.org/licenses/LICENSE-2.0> or the MIT license
// <LICENSE-MIT or http://opensource.org/licenses/MIT>, at your
// option. This file may not be copied, modified, or distributed
// except according to those terms.

//! Determine displayed width of `char` and `str` types according to
//! [Unicode Standard Annex #11](http://www.unicode.org/reports/tr11/)
//! rules.
//!
//! ```rust
//! extern crate unicode_width;
//!
//! use unicode_width::UnicodeWidthStr;
//!
//! fn main() {
//!     let teststr = "Ｈｅｌｌｏ, ｗｏｒｌｄ!";
//!     let width = UnicodeWidthStr::width(teststr);
//!     println!("{}", teststr);
//!     println!("The above string is {} columns wide.", width);
//!     let width = teststr.width_cjk();
//!     println!("The above string is {} columns wide (CJK).", width);
//! }
//! ```
//!
//! # features
//!
//! unicode-width supports a `no_std` feature. This eliminates dependence
//! on std, and instead uses equivalent functions from core.
//!
//! # crates.io
//!
//! You can use this package in your project by adding the following
//! to your `Cargo.toml`:
//!
//! ```toml
//! [dependencies]
//! unicode-width = "0.1.5"
//! ```

#![deny(missing_docs, unsafe_code)]
#![doc(html_logo_url = "https://unicode-rs.github.io/unicode-rs_sm.png",
       html_favicon_url = "https://unicode-rs.github.io/unicode-rs_sm.png")]

#![cfg_attr(feature = "bench", feature(test))]
#![no_std]

#[cfg(test)]
#[macro_use]
extern crate std;

#[cfg(feature = "bench")]
extern crate test;

pub use generated::UNICODE_VERSION;

use core::ops::Add;

mod search;
mod generated;

#[cfg(test)]
mod tests;

/// Methods for determining displayed width of Unicode characters.
pub trait UnicodeWidthChar {
    /// Returns the character's displayed width in columns, or `None` if the
    /// character is a control character other than `'\x00'`.
    ///
    /// This function treats characters in the Ambiguous category according
    /// to [Unicode Standard Annex #11](http://www.unicode.org/reports/tr11/)
    /// as 1 column wide. This is consistent with the recommendations for non-CJK
    /// contexts, or when the context cannot be reliably determined.
    fn width(self) -> Option<usize>;

    /// Returns the character's displayed width in columns, or `None` if the
    /// character is a control character other than `'\x00'`.
    ///
    /// This function treats characters in the Ambiguous category according
    /// to [Unicode Standard Annex #11](http://www.unicode.org/reports/tr11/)
    /// as 2 columns wide. This is consistent with the recommendations for
    /// CJK contexts.
    fn width_cjk(self) -> Option<usize>;
}

#[inline(always)]
fn char_width(c: char, is_cjk: bool) -> Option<usize>{
    if c < '\u{7F}' {
        if c > '\u{1F}' {
            Some(1)
        } else if c == '\0' {
            Some(0)
        } else {
            None
        }
    } else if c >= '\u{A0}' {
        let mut width = search::lookup_width(c);
        if width == 3 {
            width = if is_cjk {2} else {1}
        }
        Some(width as usize)
    }
    else {
        None
    }
}

#[inline(always)]
fn char_width_raw(c: char, is_cjk: bool) -> usize {
    let mut width = generated::HASH_TABLE[generated::hash_char(c)];
    if c < '\u{7F}' {
        return usize::from(c >= '\u{20}')
    }
    if width == 4 {
        width = search::lookup_width(c)
    }
    if width == 3 {
        width = if is_cjk {2} else {1}
    }
    return width as usize
}

impl UnicodeWidthChar for char {
    #[inline]
    fn width(self) -> Option<usize> { 
        char_width(self, false)
    }

    #[inline]
    fn width_cjk(self) -> Option<usize> { 
        char_width(self, true)
    }
}

/// Methods for determining displayed width of Unicode strings.
pub trait UnicodeWidthStr {
    /// Returns the string's displayed width in columns.
    ///
    /// Control characters are treated as having zero width.
    ///
    /// This function treats characters in the Ambiguous category according
    /// to [Unicode Standard Annex #11](http://www.unicode.org/reports/tr11/)
    /// as 1 column wide. This is consistent with the recommendations for
    /// non-CJK contexts, or when the context cannot be reliably determined.
    fn width<'a>(&'a self) -> usize;

    /// Returns the string's displayed width in columns.
    ///
    /// Control characters are treated as having zero width.
    ///
    /// This function treats characters in the Ambiguous category according
    /// to [Unicode Standard Annex #11](http://www.unicode.org/reports/tr11/)
    /// as 2 column wide. This is consistent with the recommendations for
    /// CJK contexts.
    fn width_cjk<'a>(&'a self) -> usize;
}

impl UnicodeWidthStr for str {
    #[inline]
    fn width(&self) -> usize {
        self.chars().map(|c| char_width_raw(c, false)).fold(0, Add::add)
    }

    #[inline]
    fn width_cjk(&self) -> usize {
        self.chars().map(|c| char_width_raw(c, true)).fold(0, Add::add)
    }
}
