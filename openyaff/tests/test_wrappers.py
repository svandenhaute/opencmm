import pytest
import numpy as np
import molmod
import simtk.openmm.app as app

from openyaff import YaffForceFieldWrapper, Configuration, ExplicitConversion, \
        OpenMMForceFieldWrapper
from openyaff.utils import estimate_cell_derivative, transform_symmetric, \
        wrap_coordinates, compute_lengths_angles, transform_lower_triangular, \
        reduce_box_vectors

from systems import get_system


def test_wrapper_openmm_mic():
    system, pars = get_system('mil53')
    configuration = Configuration(system, pars)
    kind = 'all'

    # YAFF and OpenMM use a different switching function. If it is disabled,
    # the results between both are identical up to 6 decimals
    configuration.switch_width = 0.0 # disable switching
    configuration.rcut = 10.0 # request cutoff of 10 angstorm
    configuration.cell_interaction_radius = 10.0
    configuration.supercell = [2, 3, 5]
    configuration.update_properties(configuration.write())
    conversion = ExplicitConversion(pme_error_thres=1e-5)
    seed_mm = conversion.apply(configuration, seed_kind=kind)
    wrapper = OpenMMForceFieldWrapper.from_seed(seed_mm, 'Reference')


    u = molmod.units.angstrom
    seed_yaff = configuration.create_seed(kind=kind)
    positions = seed_yaff.system.pos.copy() / u
    rvecs = seed_yaff.system.cell._get_rvecs().copy() / u
    e, _ = wrapper.evaluate(positions, rvecs, do_forces=True)

    # make random periodic displacements
    for i in range(5):
        coefficients = np.random.randint(-3, high=3, size=(3, 1))
        atom = np.random.randint(0, high=seed_yaff.system.natom, size=(10,))
        positions[atom, :] += np.sum(coefficients * rvecs, axis=0)
        e_ = wrapper.evaluate(positions, rvecs, do_forces=False)
        assert np.allclose(e, e_)

    # make random periodic displacements and rewrap coordinates
    for i in range(5):
        coefficients = np.random.randint(-3, high=3, size=(3, 1))
        atom = np.random.randint(0, high=seed_yaff.system.natom, size=(10,))
        positions[atom, :] += np.sum(coefficients * rvecs, axis=0)
        wrap_coordinates(positions, rvecs, rectangular=True)
        e_ = wrapper.evaluate(positions, rvecs, do_forces=False)
        assert np.allclose(e, e_)


def test_ff_yaff_periodic():
    ff = get_system('cau13', return_forcefield=True)
    wrapper = YaffForceFieldWrapper(ff)

    pos = ff.system.pos.copy()
    rvecs = ff.system.cell._get_rvecs().copy()

    nstates = 10
    pos_ = np.zeros((nstates,) + pos.shape)
    rvecs_ = np.zeros((nstates,) + (3, 3))
    energy_ = np.zeros(10)
    gpos_ = np.zeros(pos_.shape)
    for i in range(10):
        pos += np.random.uniform(-1, 1, size=pos.shape) / 5
        rvecs += np.random.uniform(-0.5, 0.5, size=rvecs.shape) / 5
        pos_[i, :] = pos[:] / molmod.units.angstrom
        rvecs_[i, :] = rvecs[:] / molmod.units.angstrom
        ff.update_pos(pos)
        ff.update_rvecs(rvecs)
        energy_[i] = ff.compute(gpos_[i, :], None)

    energy_w, force_w = wrapper.evaluate(pos_, rvecs=rvecs_, do_forces=True)
    np.testing.assert_almost_equal(
            energy_ / molmod.units.kjmol,
            energy_w,
            )
    np.testing.assert_almost_equal(
            - gpos_ / molmod.units.kjmol * molmod.units.angstrom,
            force_w,
            )


def test_ff_yaff_stress():
    def energy_func(positions, rvecs):
        ff.update_pos(positions)
        ff.update_rvecs(rvecs)
        return ff.compute()

    ff = get_system('cau13', return_forcefield=True)
    positions = ff.system.pos.copy()
    rvecs = ff.system.cell._get_rvecs().copy()
    vtens = np.zeros((3, 3))
    ff.compute(None, vtens)
    unit = molmod.units.pascal * 1e6
    pressure = np.trace(vtens) / np.linalg.det(rvecs) / unit

    dUdh = estimate_cell_derivative(positions, rvecs, energy_func, dh=1e-5)
    vtens_numerical = rvecs.T @ dUdh
    pressure_ = np.trace(vtens_numerical) / np.linalg.det(rvecs) / unit
    assert abs(pressure - pressure_) < 1e-3 # require at least kPa accuracy
    stress_  = vtens_numerical / np.linalg.det(rvecs)
    stress_ /= (molmod.units.kjmol / molmod.units.angstrom ** 3)

    wrapper = YaffForceFieldWrapper(ff)
    stress_w = wrapper.compute_stress(
            positions / molmod.units.angstrom,
            rvecs / molmod.units.angstrom,
            use_symmetric=False,
            )
    assert np.allclose(stress_w, stress_, atol=1e-5)

    # compute vtens using symmetric box, and verify compute_stress symmetric
    pos_tmp = positions.copy()
    rvecs_tmp = rvecs.copy()
    transform_symmetric(pos_tmp, rvecs_tmp)
    ff.update_pos(pos_tmp)
    ff.update_rvecs(rvecs_tmp)
    vtens = np.zeros((3, 3))
    ff.compute(None, vtens)
    stress_vtens = vtens / np.linalg.det(rvecs)
    stress_vtens /= (molmod.units.kjmol / molmod.units.angstrom ** 3)
    stress_wrapper = wrapper.compute_stress(
            positions / molmod.units.angstrom,
            rvecs / molmod.units.angstrom,
            use_symmetric=True,
            )
    assert np.allclose(stress_wrapper, stress_vtens, atol=1e-5)


def test_ff_yaff_nonperiodic():
    ff = get_system('alanine', return_forcefield=True)
    wrapper = YaffForceFieldWrapper(ff)

    pos = ff.system.pos.copy()

    nstates = 10
    pos_ = np.zeros((nstates,) + pos.shape)
    energy_ = np.zeros(10)
    gpos_ = np.zeros(pos_.shape)
    for i in range(10):
        pos += np.random.uniform(-1, 1, size=pos.shape) / 5
        pos_[i, :] = pos[:] / molmod.units.angstrom
        ff.update_pos(pos)
        energy_[i] = ff.compute(gpos_[i, :], None)

    energy_w, force_w = wrapper.evaluate(pos_, rvecs=None, do_forces=True)
    np.testing.assert_almost_equal(
            energy_ / molmod.units.kjmol,
            energy_w,
            )
    np.testing.assert_almost_equal(
            - gpos_ / molmod.units.kjmol * molmod.units.angstrom,
            force_w,
            )
