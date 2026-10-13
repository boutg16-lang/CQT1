# -*- coding: utf-8 -*-
"""Pin the CLI parser contract (extracted from main_improved into cli_args)."""

import pytest

from scripts.cli_args import build_parser


def parse(argv):
    return build_parser().parse_args(argv)


def test_build_parser_is_pure_and_repeatable():
    p1, p2 = build_parser(), build_parser()
    assert p1 is not p2
    assert p1.parse_args([]).url is None


def test_core_flags_exist_with_expected_defaults():
    args = parse([])
    assert args.url is None
    assert args.segments is None
    assert args.viral is False
    assert args.burn_only is False
    assert args.force_new_segments is False


def test_force_regenerate_alias_maps_to_same_dest():
    assert parse(["--force-new-segments"]).force_new_segments is True
    assert parse(["--force-regenerate"]).force_new_segments is True


def test_typed_and_choice_arguments():
    args = parse(["--segments", "3", "--reframe-mode", "pad"])
    assert args.segments == 3
    assert args.reframe_mode == "pad"
    with pytest.raises(SystemExit):
        parse(["--reframe-mode", "bogus"])


def test_autopilot_flag_parseable():
    assert parse(["--autopilot"]).autopilot is True
