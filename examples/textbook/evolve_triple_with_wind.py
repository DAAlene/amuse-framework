"""
   N-body integration of N particles with a Salpeter initial mass
   function between Mmin and Mmax and with stellar evolution with
   metallicity z.
"""
import sys
import numpy
from optparse import OptionParser

from prepare_figure import single_frame
from distinct_colours import get_distinct

from amuse.units import units, constants, nbody_system
from amuse.units.quantities import zero
from amuse.datamodel import Particle, Particles
from amuse.support.console import set_printing_strategy
from amuse.io import store

from amuse.ext.orbital_elements import new_binary_from_orbital_elements
from amuse.ext.orbital_elements import orbital_elements_from_binary

from amuse.community.huayno.interface import Huayno
from amuse.community.seba.interface import SeBa

def orbital_period(a, Mtot) :
    return 2*numpy.pi*(a**3/(constants.G*Mtot)).sqrt()
    
def get_orbital_elements_of_triple(stars):
    inner_binary = stars[0]+stars[1]
    outer_binary = Particles(1)
    outer_binary[0].mass = inner_binary.mass.sum()
    outer_binary[0].position = inner_binary.center_of_mass()
    outer_binary[0].velocity = inner_binary.center_of_mass_velocity()
    outer_binary.add_particle(stars[2])
    M1, M2, ain, ein, ta_in, inc_in, lan_in, aop_in \
        = orbital_elements_from_binary(inner_binary, G=constants.G)
    M12, M3, aout, eout, ta_out, outc_out, lan_out, aop_out \
        = orbital_elements_from_binary(outer_binary, G=constants.G)
    return ain, ein, aout, eout
    
def evolve_triple_with_wind(M1, M2, M3, ain_0, aout_0, ein_0, eout_0,
                            t_end, nsteps, scheme):

    from amuse.ext.solarsystem import get_position
    
    stars = Particles(3)
    stars[0].mass = M1
    stars[1].mass = M2
    stars[2].mass = M3
    stellar = SeBa()
    stellar.particles.add_particles(stars)
    channel_from_stellar = stellar.particles.new_channel_to(stars)
    stellar.evolve_model(4.6|units.Myr)
    channel_from_stellar.copy_attributes(["mass"])

    M1 = stars[0].mass
    M2 = stars[1].mass
    M3 = stars[2].mass
    # print "Masses:", M1, M2, M3
    
    # Inner binary
    
    stars = Particles(2)
    stars[0].mass = M1
    stars[1].mass = M2

    Pin = orbital_period(ain_0, M1+M2)
    print 'ain_0 =', ain_0
    print 'M1+M2 =', M1+M2
    print 'Pin =', Pin
    print 'Pin =', Pin.value_in(units.day), '[day]'

    dt = 0.1*Pin
    ma = 180
    inc = 30
    aop = 180
    lon = 0
    r, v = get_position(M1, M2, ein_0, ain_0, ma,inc,aop,lon,dt)
    stars[1].position = r
    stars[1].velocity = v
    stars.move_to_center()

    # Outer binary
    
    r, v = get_position(M1+M2, M3, eout_0, aout_0, 0,0,0,0,dt)
    tertiary = Particle()
    tertiary.mass = M3
    tertiary.position = r
    tertiary.velocity = v
    stars.add_particle(tertiary)
    stars.move_to_center()

    Mstars = stars.mass.sum()
    Pout = orbital_period(aout_0, Mstars)

    stellar = SeBa()
    stellar.particles.add_particles(stars)
    stellar.evolve_model(4|units.Myr)
    channel_from_stellar = stellar.particles.new_channel_to(stars)
    channel_from_stellar.copy_attributes(["mass"])

    converter = nbody_system.nbody_to_si(stars.mass.sum(), aout_0)
    gravity = Huayno(converter)
    gravity.parameters.timestep_parameter = 0.01
    gravity.particles.add_particles(stars)

    channel_from_framework_to_gd = stars.new_channel_to(gravity.particles)
    channel_from_gd_to_framework = gravity.particles.new_channel_to(stars)
    
    Etot_init = gravity.kinetic_energy + gravity.potential_energy
    Etot_prev = Etot_init

    gravity.particles.move_to_center()

    time = 0.0 | t_end.unit
    ts = (4.0|units.Myr) + time

    ain, ein, aout, eout = get_orbital_elements_of_triple(stars)
    print "Triple elements t=",  time,  \
        "inner:", stars[0].mass, stars[1].mass, ain, ein, \
        "outer:", stars[2].mass, aout, eout

    dt_diag = t_end/float(nsteps)
    t_diag = dt_diag
    dtse_fac = 50

    t = [time.value_in(units.Myr)] 
    smai = [ain/ain_0] 
    ecci = [ein/ein_0]
    smao = [aout/aout_0] 
    ecco = [eout/eout_0]

    def advance_stellar(ts, dt):
        E0 = gravity.kinetic_energy + gravity.potential_energy
        ts += dt
        stellar.evolve_model(ts)
        channel_from_stellar.copy_attributes(["mass"])
        channel_from_framework_to_gd.copy_attributes(["mass"])
        return ts, gravity.kinetic_energy + gravity.potential_energy - E0

    def advance_gravity(tg, dt):
        tg += dt
        gravity.evolve_model(tg)
        channel_from_gd_to_framework.copy()
        return tg

    while time < t_end:

        Pin = orbital_period(ain, stars[0].mass)
        dt = dtse_fac*Pin

        if scheme == 1:
            
            ts, dE_se = advance_stellar(ts, dt)
            time = advance_gravity(time, dt)
            
        elif scheme == 2:
            
            time = advance_gravity(time, dt)
            ts, dE_se = advance_stellar(ts, dt)
            
        else:

            ts, dE_se = advance_stellar(ts, dt/2)
            time = advance_gravity(time, dt)
            ts, dE = advance_stellar(ts, dt/2)
            dE_se += dE

        if time >= t_diag:
            
            t_diag = time + dt_diag

            Ekin = gravity.kinetic_energy 
            Epot = gravity.potential_energy
            Etot = Ekin + Epot
            dE = Etot_prev - Etot
            Mtot = stars.mass.sum()
            print "T=", time, 
            print "M=", Mtot, "(dM[SE]=", Mtot/Mstars, ")",
            print "E= ", Etot, "Q= ", Ekin/Epot,
            print "dE=", (Etot_init-Etot)/Etot, "ddE=", (Etot_prev-Etot)/Etot, 
            print "(dE[SE]=", dE_se/Etot, ")"
            Etot_init -= dE
            Etot_prev = Etot
            ain, ein, aout, eout = get_orbital_elements_of_triple(stars)
            print "Triple elements t=",  (4|units.Myr) + time,  \
                "inner:", stars[0].mass, stars[1].mass, ain, ein, \
                "outer:", stars[2].mass, aout, eout

            t.append(time.value_in(units.Myr))
            smai.append(ain/ain_0)
            ecci.append(ein/ein_0)
            smao.append(aout/aout_0)
            ecco.append(eout/eout_0)

            if stellar.particles[0].mass <= 0.1|units.MSun:
                break

            if eout > 1.0 or aout <= zero:
                print "Binary ionized or merged"
                break

    gravity.stop()
    stellar.stop()

    return t, smai, ecci, smao, ecco

def main(M1, M2, M3, ain, aout, ein, eout, t_end, nsteps, scheme):

    from matplotlib import pyplot
    
    x_label = "$a/a_{0}$"
    y_label = "$e/e_{0}$"
    fig = single_frame(x_label, y_label, logx=False, logy=False,
                       xsize=10, ysize=8)
    color = get_distinct(4)

    if scheme == 0:
        srange = [1,2,3]
    else:
        srange = [scheme]

    i = 0
    for s in srange:
        time, ai, ei, ao, eo = evolve_triple_with_wind(M1, M2, M3,
                                                       ain, aout,
                                                       ein, eout,
                                                       t_end, nsteps, s)
        if i == 0:
            pyplot.plot(ai, ei, c=color[0], label='inner')
            pyplot.plot(ao, eo, c=color[1], label='outer')
            i = 1
        else:
            pyplot.plot(ai, ei, c=color[0])
            pyplot.plot(ao, eo, c=color[1])

    pyplot.legend(loc='best', ncol=1, shadow=False, fontsize=20)
    
    save_file = 'evolve_triple_with_wind.png'
    pyplot.savefig(save_file)
    print '\nSaved figure in file', save_file,'\n'
    pyplot.show()
    
def new_option_parser():
    
    from amuse.units.optparse import OptionParser
    result = OptionParser()
    result.add_option("-n",
                      dest="nsteps", type="int", default = 1000,
                      help="diagnostic time steps [%default]")

    # scheme: 1 = SE first, 2 = gravity first, 3 = interlaced, 0 = all

    result.add_option("-s",
                      dest="scheme", type="int", default = 3,
                      help="integration scheme [%default]")
    result.add_option("--M1", unit=units.MSun,
                      dest="M1", type="float",default = 60 | units.MSun,
                      help="Primary mass [%default]")
    result.add_option("--M2", unit=units.MSun,
                      dest="M2", type="float",default = 30 | units.MSun,
                      help="secondary mass [%default]")
    result.add_option("--M3", unit=units.MSun,
                      dest="M3", type="float",default = 20 | units.MSun,
                      help="secondary mass [%default]")
    result.add_option("--ain", unit=units.AU,
                      dest="ain", type="float",default = 0.63|units.AU,
                      help="orbital separation [%default]")
    result.add_option("--aout", unit=units.AU,
                      dest="aout", type="float",default = 100|units.AU,
                      help="orbital separation [%default]")
    result.add_option("--ein", dest="ein", type="float", default = 0.2,
                      help="orbital eccentricity [%default]")
    result.add_option("--eout", dest="eout", type="float", default = 0.6,
                      help="orbital eccentricity [%default]")
    result.add_option("-t", unit=units.Myr,
                      dest="t_end", 
                      type="float", default = 0.55 | units.Myr,
                      help="end time of the simulation [%default]")
    return result

if __name__ in ('__main__', '__plot__'):
    
    set_printing_strategy("custom", 
                          preferred_units = [units.MSun, units.AU, units.Myr], 
                          precision = 12, prefix = "", 
                          separator = " [", suffix = "]")

    o, arguments  = new_option_parser().parse_args()
    main(**o.__dict__)

