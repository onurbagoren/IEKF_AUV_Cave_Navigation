from import_data import imu_data, dvl_data, imu_bias_data, depth_data, odom_data, initial_pose, mag_data
import numpy as np
from scipy.linalg import  expm, block_diag
from riekf import Right_IEKF
from plot_ekf_results import plot_time_series, plot_2d, plot_3d
from localization_metrics import *
import matplotlib.pyplot as plt
import sys

def skew(omega):
    # Assume phi is a 3x1 vector
    return np.array([[        0, -omega[2],  omega[1]],
                     [ omega[2],         0, -omega[0]],
                     [-omega[1],  omega[0],         0]])

def gamma_0(phi):
    '''
    Assume phi comes in vector form
    '''
    return expm(skew(phi))

def gamma_1(phi):
    '''
    Assume phi comes in vector form
    '''
    nphi = np.linalg.norm(phi)
    sphi = skew(phi)
    return np.eye(3) + ((1-np.cos(nphi))/(nphi**2))*sphi + ((nphi-np.sin(nphi))/(nphi**3))*np.matmul(sphi,sphi)

def gamma_2(phi):
    '''
    Assume phi comes in vector form
    '''
    nphi = np.linalg.norm(phi)
    sphi = skew(phi)
    return 0.5*np.eye(3) + ((nphi-np.sin(nphi))/(nphi**3))*sphi + ((nphi**2 + 2*np.cos(nphi) - 2)/(2*(nphi**4)))*np.matmul(sphi,sphi)

def imu_dynamics(state, inputs, dt):
    Rk = state[:3,:3]
    vk = state[:3,3]
    pk = state[:3,4]
    # Assume matrix form for inputs
    omega_k = inputs[:3,:3]   #3x3
    ak = inputs[:3,3]         #3x1

    g = np.array([0, 0, -9.80665])  # may need to make negative

    Rk1 = np.matmul(Rk,expm(omega_k*dt))
    vk1 = vk + np.dot(Rk,ak)*dt + g*dt  # vk + np.dot(np.matmul(Rk,np.eye(3)),ak)*dt + g*dt
    pk1 = pk + vk*dt + np.dot(Rk,0.5*ak)*(dt**2) + 0.5*g*(dt**2)  # pk + vk*dt + np.dot(np.dot(Rk,0.5*np.eye(3)),ak)*(dt**2) + 0.5*g*(dt**2)

    new_state = np.eye(5)
    new_state[:3,:3] = Rk1
    new_state[:3,3]  = vk1
    new_state[:3,4]  = pk1
    return new_state

def A_matrix():
    A = np.zeros((9,9))
    g = np.array([0, 0, -9.80665])
    A[3:6,0:3] = skew(g)
    A[6:,3:6] = np.eye(3)
    return A

def H_matrix(b): # Possibly nonconstant, but not likely we believe
    '''
    H is 5x9 real matrix satisfying
    H*Xi = -Xi^{\wedge}*b, where we expect 
    Xi = [Xi_omega1 Xi_omega2 Xi_omega3 Xi_a1 Xi_a2 Xi_a3 Xi_v1 Xi_v2 Xi_v3]^T
    and
    b = [0 0 0 1 0]^T
    where Xi_ak is the k acceleration component, corresponding to the k velocity
    component of the state, and likewise for Xi_vj to pj in the position.
    '''
    H = np.zeros((3,9))
    H[:,3:6] = -np.eye(3)
    return H

def H_matrix_dvl_depth(b):
    H = np.zeros((5,9))
    H[:3,3:6] = -np.eye(3)
    H[:3,6:] = -np.eye(3)
    return H

def H_stacked(b):
    # Exploit the fact we want/have zero-rows to get a smaller matrix
    H = np.zeros((7,9))
    H[:3,3:6] = -np.eye(3)
    H[3,8] = -1
    H[4:,:3] = skew(b[10:13])
    return H

def floating_mean(data, index, window):
    if index > window:
        floating_mean = np.zeros_like(data[:,index])
        for i in range(window):
            floating_mean += data[:,(index-window+1+i)]
        floating_mean = floating_mean/window
    else:
        floating_mean = data[:,index]
    return floating_mean

def toy_example():
    dummy_input = np.zeros((9,1))  # input comes in form of 9x1 vector
    dummy_input[3] = 0.1  # 0.1 m/s^2 for linear acceleration in x
    dummy_input[5] = -9.80665  # stand-in for gravity
    dummy_correction = np.array([-0.1, 0, 0, 1, 0]).T  # given dt = 1, should be -0.1 m/s in x direction as observed from DVL
    b = np.array([0, 0, 0, 1, 0]).T  # second to last element needs to be 1 because of homogeneous tranformation formulation

    Q_omega = 0.1*np.eye(3)
    Q_a = 0.1*np.eye(3)
    Q = block_diag(Q_omega, Q_a, np.eye(3))

    DVL_cov = 0.1*np.eye(3)
    N = block_diag(DVL_cov, np.eye(2))  # needs to be 5x5 to match

    system = {
        'f': imu_dynamics,
        'A': A_matrix(),
        'H': H_matrix,
        'Q': Q,
        'N': N,
    }
    # print(expm(A_matrix()*0.01))
    filt = Right_IEKF(system)
    filt.prediction(dummy_input, 1)
    print(filt.X)
    filt.correction(dummy_correction, b)
    print(filt.X)

def run_IEKF_caves():

    Q_omega = 0.1*np.eye(3)
    Q_a = 0.1*np.eye(3)
    Q = block_diag(Q_omega, Q_a, np.matmul(Q_a.T,Q_a))  # Last block is velocity covariance, so will be noisier if acceleration is noisy

    # needs to be 5x5 to match, check if diagonalization is correct here (according to se2, rest should be zero. 
    # However, when we do that in practice the correction step gives us a singular matrix?). 
    # Permutation of axes does not matter as long as we have a diagonal covariance.
    DVL_cov = 0.7*np.eye(3)
    N_DVL = block_diag(DVL_cov, np.zeros((2,2)))  

    D_cov = 1000*np.eye(3)
    N_D = block_diag(D_cov, np.zeros((2,2)))

    M_cov = 1000*np.eye(3)
    N_M = block_diag(M_cov, np.zeros((2,2)))

    X0 = np.eye(5)
    X0[:3,:3] = initial_pose.R
    X0[:3,4] = initial_pose.p

    b = np.array([0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0.24494, -0.002385, -0.38615, 0, 0]).T  # experimental, for stacking dvl and depth measurements
    system = {
        'f': imu_dynamics,
        'A': A_matrix(),
        'H': H_stacked,
        'Q': Q,
        'N_DVL': N_DVL,
        'N_D': N_D,
        'N_M': N_M,
        'X': X0,
    }

    imu_to_dvl = np.array([0, 0.38, 0.07]).T
    d_skew = skew(imu_to_dvl)

    filt = Right_IEKF(system)
    # dt is from 0, so just need first timestep
    dt1 = imu_data.time[0,0] * 1e-9

    # Need to collect predictions over time and ground truth (gt)
    all_X_pred = []
    all_X_elem_pred = []
    all_time_pred = []
    all_X_gt = odom_data.z.T 
    all_time_gt = odom_data.time[0, :] * 1e-9
    b_g = imu_bias_data.z

    filt.prediction(imu_data.z[:,0], 0.1)

    imu_ind = 1
    dvl_ind = 0
    depth_ind = 0
    latest_time = min(imu_data.time[0, 0], dvl_data.time[0, 0], depth_data.time[0, 0]) * 1e-9
    while imu_ind < imu_data.l and dvl_ind < dvl_data.l and depth_ind < depth_data.l:

        # Naive matching here, merely to avoid correcting with something that came beforehand
        if imu_data.time[0,imu_ind] < dvl_data.time[0,dvl_ind]:
            dt = imu_data.time[0,imu_ind] - imu_data.time[0,imu_ind-1]
            dt = dt * 1e-9
            
            filt.prediction(floating_mean(imu_data.z, imu_ind, 15), dt)
            imu_ind += 1

            latest_time = imu_data.time[0,imu_ind] * 1e-9
        else:
            # Synthesize robot frame "depth" measurement
            depth_position = filt.X[:3,4]
            while depth_data.time[0,depth_ind] < dvl_data.time[0,dvl_ind]:
                depth_position[2] = -depth_data.z[0,depth_ind]
                depth_ind += 1
            depth_position = -np.matmul(filt.X[:3,:3].T, depth_position)

            twist_velocity = np.matmul( d_skew, (imu_data.z[:3,imu_ind]) )

            measurement = np.hstack((dvl_data.z[:,dvl_ind]-twist_velocity, [1, 0], depth_position, [0, 1], mag_data.z[:,imu_ind], [0, 0]))
            filt.correction_stacked(measurement, b)
            dvl_ind += 1

        # Save prediction from this iteration
        all_X_pred.append(filt.X[:3, 4])
        all_X_elem_pred.append(filt.unskew(filt.X))
        all_time_pred.append(latest_time)

    all_X_pred = np.array(all_X_pred)
    all_X_elem_pred = np.array(all_X_elem_pred)[:,:,0]
    all_time_pred = np.array(all_time_pred)

    np.savetxt(f'{sys.path[0]}/all_X_elem_pred.txt', all_X_elem_pred)
    
    # Compare results to slam solution
    # slam = []
    # with open(os.path.join('comparison_results', 'slam.csv')) as csvf:
    #     reader = csv.reader(csvf)
    #     for row in reader:
    #         slam.append([float(s) for s in row])
    # slam = np.array(slam).T # Note their results are in DVL frame
    # slam_times = np.array(slam)[:, 0]
    # slam = slam[:, 1:]
    # slam[:, 1:] = -slam[:, 1:]

    # all_times = [all_time_pred, all_time_gt, slam_times]

    # # print(all_X_gt[0, :])

    # metrics_us = cone_metrics(all_X_pred, all_time_pred)
    # metrics_gt = cone_metrics(all_X_gt[:, :3], all_time_gt)
    # metrics_slam = cone_metrics(slam, slam_times)

    # print('Our Cone Pass Differences:\n')
    # for cone in range(6):
    #     print(cone, metrics_us['%s_2pass_2norm' % str(cone)])

    # print('\nVisual-Odometry Cone Pass Differences:\n')
    # for cone in range(6):
    #     print(cone, metrics_gt['%s_2pass_2norm' % str(cone)])

    # print('\nSLAM Cone Pass Differences:\n')
    # for cone in range(6):
    #     print(cone, metrics_slam['%s_2pass_2norm' % str(cone)])

    # print('----')
    # print(metrics_us)
    # print('----')
    # print(metrics_gt)
    # print('----')
    # print(metrics_slam)

    # Plot 3D position graph to check results
    # plot_3d([all_X_pred[:, 0], all_X_gt[:, 0], slam[:, 0]], 
    #         [all_X_pred[:, 1], all_X_gt[:, 1], slam[:, 1]], 
    #         [all_X_pred[:, 2], all_X_gt[:, 2], slam[:, 2]],
    #         'orientation_x', 'orientation_y', 'orientation_z',
    #         ['predicted', 'odometry', 'slam'],
    #         'RI-EKF Results',
    #         save_dir='../',
    #         state_times=all_times)

    # plot_2d([all_X_pred[:, 0], all_X_gt[:, 0], slam[:, 0]], 
    #         [all_X_pred[:, 1], all_X_gt[:, 1], slam[:, 1]],
    #         'orientation_x', 'orientation_y', 
    #         ['predicted', 'ground truth', 'slam'],
    #         'RI-EKF Results',
    #         save_dir='../',
    #         state_times=all_times)


def plot_states(vec):
    '''
    :param vec N X 9 numpy array
    '''

    tx = vec[:, 0] # assuming that this is the orientation (theta == t)
    ty = vec[:, 1]
    tz = vec[:, 2]
    vx = vec[:, 3]
    vy = vec[:, 4]
    vz = vec[:, 5]
    x = vec[:, 6]
    y = vec[:, 7]
    z = vec[:, 8]

    fig, ax = plt.subplots(3, 3, figsize=(15, 15))
    ax[0, 0].plot(tx, '.')
    ax[0, 0].set_title('tx')
    ax[0, 1].plot(ty, '.')
    ax[0, 1].set_title('ty')
    ax[0, 2].plot(tz, '.')
    ax[0, 2].set_title('tz')
    ax[1, 0].plot(vx, '.')
    ax[1, 0].set_title('vx')
    ax[1, 1].plot(vy, '.')
    ax[1, 1].set_title('vy')
    ax[1, 2].plot(vz, '.')
    ax[1, 2].set_title('vz')
    ax[2, 0].plot(x, '.')
    ax[2, 0].set_title('x')
    ax[2, 1].plot(y, '.')
    ax[2, 1].set_title('y')
    ax[2, 2].plot(z, '.')
    ax[2, 2].set_title('z')
    plt.show()



if __name__ == "__main__":
    run_IEKF_caves()
