'Miscellaneous estimation and data-massaging functions.'
from __future__ import absolute_import, division, print_function
import numpy as np
from logging import getLogger
import scipy.optimize
import scipy.interpolate
import multiprocessing as mp
from collections import namedtuple
import contextlib

from . import _smcpp

logger = getLogger(__name__)


@contextlib.contextmanager
def mp_pool():
    # Safely destroy multiprocessing pool to avoid annoying stack traces
    # on KeyboardInterrupt
    p = mp.Pool()
    yield p
    p.close()
    p.join()
    p.terminate()


# Construct time intervals stuff
def extract_pieces(piece_str):
    '''Convert PSMC-style piece string to model representation.'''
    pieces = []
    for piece in piece_str.split("+"):
        try:
            num, span = list(map(int, piece.split("*")))
        except ValueError:
            span = int(piece)
            num = 1
        pieces += [span] * num
    return pieces


def construct_time_points(t1, tK, pieces, offset):
    s = np.diff(np.logspace(np.log10(offset + t1),
                            np.log10(tK),
                            sum(pieces) + 1))
    time_points = np.zeros(len(pieces))
    count = 0
    for i, p in enumerate(pieces):
        time_points[i] = s[count:(count+p)].sum()
        count += p
    return np.concatenate([[t1], time_points])


def compress_repeated_obs(dataset):
    # pad with illegal value at starting position
    nonce = np.zeros_like(dataset[0])
    nonce[:2] = [1, -999]
    dataset = np.concatenate([[nonce], dataset, [nonce]])
    nonreps = np.any(dataset[1:, 1:] != dataset[:-1, 1:], axis=1)
    newob = dataset[1:][nonreps]
    csw = np.cumsum(dataset[:, 0])[np.where(nonreps)]
    newob[:-1, 0] = csw[1:] - csw[:-1]
    return newob[:-1]


def _thin_helper(args):
    thinned = np.array(_smcpp.thin_data(*args), dtype=np.int32)
    return compress_repeated_obs(thinned)


def thin_dataset(dataset, thinning):
    '''Only emit full SFS every <thinning> sites'''
    with mp_pool() as p:
        return list(p.map(_thin_helper, [(chrom, thinning, i)
                                         for i, chrom in enumerate(dataset)]))


def break_long_spans(dataset, rho, length_cutoff):
    # Spans longer than this are broken up
    # FIXME: should depend on rho
    span_cutoff = 1000000
    obs_list = []
    obs_attributes = {}
    for fn, obs in enumerate(dataset):
        miss = np.zeros_like(obs[0])
        miss[0] = 1
        miss[1::3] = -1
        long_spans = np.where(
            (obs[:, 0] >= span_cutoff) &
            np.all(obs[:, 1::3] == -1, axis=1) &
            np.all(obs[:, 3::3] == 0, axis=1))[0]
        cob = 0
        if obs[long_spans].size:
            logger.info("Long missing spans: \n%s", (obs[long_spans]))
        positions = np.insert(np.cumsum(obs[:, 0]), 0, 0)
        for x in long_spans.tolist() + [None]:
            s = obs[cob:x, 0].sum()
            if s > length_cutoff:
                obs_list.append(np.insert(obs[cob:x], 0, miss, 0))
                l = obs_list[-1][:, 0].sum()
                s2 = obs_list[-1][:, 1][obs_list[-1][:, 1] >= 0].sum()
                obs_attributes.setdefault(fn, []).append(
                        (positions[cob],
                         positions[x] if x is not None else positions[-1],
                         l, 1. * s2 / l))
            else:
                logger.info("omitting sequence length < %d "
                            "as less than length cutoff %d" %
                            (s, length_cutoff))
            try:
                cob = x + 1
            except TypeError:  # fails for final x=None
                pass
    return obs_list, obs_attributes


def balance_hidden_states(model, M):
    M -= 1
    eta = _smcpp.PyRateFunction(model, [])
    ret = [0.0]
    # ms = np.arange(0.1, 2.1, .1).tolist() + list(range(3, M))
    ms = range(1, M)
    for m in ms:
        def f(t):
            Rt = float(eta.R(t))
            return np.exp(-Rt) - 1.0 * (M - m) / M
        res = scipy.optimize.brentq(f, ret[-1], 1000.)
        ret.append(res)
    ret.append(np.inf)
    return np.array(ret)


def empirical_sfs(data, n, a):
    with mp_pool() as p:
        return np.sum(list(p.map(_esfs_helper, ((d, n, a) for d in data))), axis=0)


def _esfs_helper(tup):
    ds, n, a = tup
    shp = [x + 1 for na in zip(a, n) for x in na]
    ret = np.zeros(shp, dtype=int)
    nmiss = np.where(np.all(ds[:, 1::3] >= 0, axis=1) & np.all(ds[:, 3::3] == n, axis=1))
    for row in ds[nmiss]:
        coord = tuple([x for ab in zip(row[1::3], row[2::3]) for x in ab])
        ret[coord] += row[0]
    return ret
