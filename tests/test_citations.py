"""Citation parsing: gpt-oss models cite with full-width brackets, which used to be missed."""

from knowledge.answer import cited_numbers


def test_square_brackets():
    assert cited_numbers("Use git push [2]. See also [1][2].", 5) == [2, 1]


def test_full_width_brackets_from_gpt_oss():
    assert cited_numbers("Fetch first 【1】 then rebase 【3†L10-L12】.", 5) == [1, 3]


def test_lists_and_out_of_range():
    assert cited_numbers("Both say so [1, 4; 9] and 【2,3】.", 5) == [1, 4, 2, 3]


def test_no_citation_and_plain_numbers():
    assert cited_numbers("Run git status 2 times, list [a].", 5) == []
