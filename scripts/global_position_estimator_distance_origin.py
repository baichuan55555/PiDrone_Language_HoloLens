"""""
global_position_estimator_distance

Localization helper code for picam_localization_distance
"""""

import math
import numpy as np
import cv2

# ----- parameters are same as picam_localization -----
CAMERA_WIDTH = 320
CAMERA_HEIGHT = 240
MAP_PIXEL_WIDTH = 2048  # in pixel
MAP_PIXEL_HEIGHT = 1616
MAP_REAL_WIDTH = 1.4  # in meter
MAP_REAL_HEIGHT = 1.07

METER_TO_PIXEL = (float(MAP_PIXEL_WIDTH) / MAP_REAL_WIDTH + float(MAP_PIXEL_HEIGHT) / MAP_REAL_HEIGHT) / 2.
CAMERA_CENTER = np.float32([(CAMERA_WIDTH - 1) / 2., (CAMERA_HEIGHT - 1) / 2.]).reshape(-1, 1, 2)
CAMERA_SCALE = 280.

# ----- parameters for features -----
ORB_GRID_SIZE_X = 4
ORB_GRID_SIZE_Y = 3
MATCH_RATIO = 0.7  # can be tuned, taken from opencv tutorial
MIN_MATCH_COUNT = 10  # can be tuned, taken from opencv tutorial
MAP_GRID_SIZE_X = ORB_GRID_SIZE_X * 3
MAP_GRID_SIZE_Y = ORB_GRID_SIZE_Y * 3
CELL_X = float(MAP_PIXEL_WIDTH) / MAP_GRID_SIZE_X
CELL_Y = float(MAP_PIXEL_HEIGHT) / MAP_GRID_SIZE_Y
PROB_THRESHOLD = 0.001
MEASURE_WAIT_COUNT = 5
MAP_FEATURES = 600


class Particle(object):
    """
    each particle holds poses and weights of all particles
    z is currently not used
    """

    def __init__(self, i, poses, weights):
        self.i = i
        self.poses = poses
        self.weights = weights

    def weight(self): return self.weights[self.i]

    def x(self): return self.poses[self.i, 0]

    def y(self): return self.poses[self.i, 1]

    def z(self): return self.poses[self.i, 2]

    def yaw(self): return self.poses[self.i, 3]

    def __str__(self):
        return str(self.x()) + ' , ' + str(self.y()) + ' weight ' + str(self.weight())

    def __repr__(self):
        return str(self.x()) + ' , ' + str(self.y()) + ' weight ' + str(self.weight())


class ParticleSet(object):
    def __init__(self, num_particles, poses):
        self.weights = np.full(num_particles, PROB_THRESHOLD)
        self.particles = [Particle(i, poses, self.weights) for i in range(num_particles)]
        self.poses = poses
        self.num_particles = num_particles


class LocalizationParticleFilter:
    """
    Particle filter for localization.
    """

    def __init__(self, map_kp, map_des):
        self.map_kp = map_kp
        self.map_des = map_des

        self.particles = None
        self.measure_count = 0

        index_params = dict(algorithm=6, table_number=6, key_size=12, multi_probe_level=1)
        search_params = dict(checks=50)
        self.matcher = cv2.FlannBasedMatcher(index_params, search_params)

        self.previous_time = None

        self.z = 0.0
        self.angle_x = 0.0
        self.angle_y = 0.0

        # parameters - noise on sensor
        # TODO check the parameters and covariance matrix is proper

        self.sigma_x = 0.05
        self.sigma_y = 0.05
        self.sigma_yaw = 0.01

        sigma_vx = 0.01
        sigma_vy = 0.01
        sigma_vz = 0.0
        sigma_yaw = 0.01
        self.covariance_motion = np.array([[sigma_vx ** 2, 0, 0, 0],
                                           [0, sigma_vy ** 2, 0, 0],
                                           [0, 0, sigma_vz ** 2, 0],
                                           [0, 0, 0, sigma_yaw ** 2]])

    def sample_motion_model(self, x, y, yaw):
        """
        Implement motion model from Equation 3 in PiDrone Slam with noise.
        """
        # add noise
        noisy_x_y_z_yaw = np.random.multivariate_normal([x, y, self.z, yaw], self.covariance_motion)

        for i in range(self.particles.num_particles):
            pose = self.particles.poses[i]
            old_yaw = pose[3]
            pose[0] += (noisy_x_y_z_yaw[0] * np.cos(old_yaw) + noisy_x_y_z_yaw[1] * np.sin(old_yaw))
            pose[1] += (noisy_x_y_z_yaw[0] * np.sin(old_yaw) + noisy_x_y_z_yaw[1] * np.cos(old_yaw))
            pose[2] = self.z
            pose[3] += noisy_x_y_z_yaw[3]
            pose[3] = adjustAngle(pose[3])

    def measurement_model(self, kp, des):
        """
        landmark_model_known_correspondence from probablistic robotics 6.6
        """
        for i in range(self.particles.num_particles):
            position = self.particles.poses[i]

            # get grid x and y
            grid_x = int(position[0] * METER_TO_PIXEL / CELL_X)
            grid_x = max(min(grid_x, MAP_GRID_SIZE_X - 1), 0)
            grid_y = int(position[1] * METER_TO_PIXEL / CELL_Y)
            grid_y = max(min(grid_y, MAP_GRID_SIZE_Y - 1), 0)
            # get 8 map cells around pose
            sub_map_kp = self.map_kp[grid_x][grid_y]
            sub_map_des = self.map_des[grid_x][grid_y]

            # measure current global position
            pose, num = self.compute_location(kp, des, sub_map_kp, sub_map_des)

            # compute weight of particle
            if pose is None:
                q = PROB_THRESHOLD
            else:
                # add noise
                noisy_pose = [np.random.normal(pose[0], self.sigma_x), np.random.normal(pose[1], self.sigma_y), pose[2],
                              np.random.normal(pose[3], self.sigma_yaw)]

                noisy_pose[3] = adjustAngle(noisy_pose[3])

                yaw_difference = noisy_pose[3] - position[3]
                yaw_difference = adjustAngle(yaw_difference)

                q = norm_pdf(noisy_pose[0] - position[0], 0, self.sigma_x) \
                    * norm_pdf(noisy_pose[1] - position[1], 0, self.sigma_y) \
                    * norm_pdf(yaw_difference, 0, self.sigma_yaw)

            self.particles.weights[i] = max(q, PROB_THRESHOLD)

    def update(self, z, angle_x, angle_y, prev_kp, prev_des, kp, des):
        """
        We implement the MCL algorithm from probabilistic robotics (Table 8.2)
        kp is the position of detected features
        des is the description of detected features
        """
        # update parameters
        self.z = z
        self.angle_x = angle_x
        self.angle_y = angle_y

        transform = self.compute_transform(prev_kp, prev_des, kp, des)
        if transform is not None:
            # these are the differences, not global
            x = -transform[0, 2] * self.z / CAMERA_SCALE
            y = transform[1, 2] * self.z / CAMERA_SCALE
            yaw = -np.arctan2(transform[1, 0], transform[0, 0])
            self.sample_motion_model(x, y, yaw)

        if self.measure_count > MEASURE_WAIT_COUNT:
            self.measurement_model(kp, des)
            self.measure_count = 0

        self.measure_count += 1

        self.resample_particles()

        return self.get_estimated_position()

    def resample_particles(self):
        """""
        samples a new particle set, biased towards particles with higher weights
        """""
        weights_sum = np.sum(self.particles.weights)
        new_poses = []
        new_weights = []

        normal_weights = self.particles.weights / float(weights_sum)  # normalize
        # samples sums to num_particles with same length as normal_weights, positions with higher weights are
        # more likely to be sampled
        samples = np.random.multinomial(self.particles.num_particles, normal_weights)
        for i, count in enumerate(samples):
            for _ in range(count):
                new_poses.append(self.particles.poses[i])
                new_weights.append(self.particles.weights[i])

        self.particles.poses = np.array(new_poses)
        self.particles.weights = np.array(new_weights)

    def get_estimated_position(self):
        """""
        retrieves the drone's estimated position
        """""
        weights_sum = np.sum(self.particles.weights)
        x = 0.0
        y = 0
        z = 0
        yaw = 0

        normal_weights = self.particles.weights / float(weights_sum)  # get the probability
        for i, prob in enumerate(normal_weights):
            x += prob * self.particles.poses[i, 0]
            y += prob * self.particles.poses[i, 1]
            z += prob * self.particles.poses[i, 2]
            yaw += prob * self.particles.poses[i, 3]

        return Particle(0, np.array([[x, y, z, yaw]]), np.array([weights_sum / self.particles.weights.size]))

    def initialize_particles(self, num_particles, kp, des):
        """
        find most possible location to start
        :param num_particles: number of particles we are using
        :param kp: the keyPoints of the first captured image
        :param des: the descriptions of the first captured image
        """
        weights_sum = 0.0
        weights = []
        poses = []
        new_poses = []

        # go through every grid, trying to find matched features
        for x in range(MAP_GRID_SIZE_X):
            for y in range(MAP_GRID_SIZE_Y):
                p, w = self.compute_location(kp, des, self.map_kp[x][y], self.map_des[x][y])
                if p is not None:
                    poses.append([p[0], p[1], p[2], p[3]])
                    weights.append(w)
                    weights_sum += w

        # cannot find a match
        if len(poses) == 0:
            print "Random Initialization"
            for x in range(MAP_GRID_SIZE_X):
                for y in range(MAP_GRID_SIZE_Y):
                    poses.append([(x * CELL_X + CELL_X / 2.0) / METER_TO_PIXEL,
                                  (y * CELL_Y + CELL_Y / 2.0) / METER_TO_PIXEL,
                                   self.z,
                                   np.random.random_sample() * 2 * np.pi - np.pi])
                    weights_sum += 1.0  # uniform sample
                    weights.append(1.0)

        # sample particles based on the number of matched features
        weights = np.array(weights) / weights_sum  # normalize
        samples = np.random.multinomial(num_particles, weights)  # sample
        for i, count in enumerate(samples):
            for _ in range(count):
                new_poses.append(poses[i])

        self.particles = ParticleSet(num_particles, np.array(new_poses))
        return self.get_estimated_position()

    def compute_location(self, kp1, des1, kp2, des2):
        """
        compute the global location of center of current image
        :param kp1: captured keyPoints
        :param des1: captured descriptions
        :param kp2: map keyPoints
        :param des2: map descriptions
        :return: global pose
        """

        good = []
        pose = None

        if des1 is not None and des2 is not None:
            matches = self.matcher.knnMatch(des1, des2, k=2)

            for match in matches:
                if len(match) > 1 and match[0].distance < MATCH_RATIO * match[1].distance:
                    good.append(match[0])

            if len(good) > MIN_MATCH_COUNT:
                src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
                dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
                transform = cv2.estimateRigidTransform(src_pts, dst_pts, False)
                if transform is not None:
                    transformed_center = cv2.transform(CAMERA_CENTER, transform)  # get global pixel
                    transformed_center = [transformed_center[0][0][0] / METER_TO_PIXEL,  # map to global pose
                                          (MAP_PIXEL_HEIGHT - 1 - transformed_center[0][0][1]) / METER_TO_PIXEL]
                    yaw = np.arctan2(transform[1, 0], transform[0, 0])  # get global heading

                    # correct the pose if the drone is not level
                    z = math.sqrt(self.z ** 2 / (1 + math.tan(self.angle_x) ** 2 + math.tan(self.angle_y) ** 2))
                    offset_x = np.tan(self.angle_x) * z
                    offset_y = np.tan(self.angle_y) * z
                    global_offset_x = math.cos(yaw) * offset_x + math.sin(yaw) * offset_y
                    global_offset_y = math.sin(yaw) * offset_x + math.cos(yaw) * offset_y
                    pose = [transformed_center[0] + global_offset_x, transformed_center[1] + global_offset_y, z, yaw]

        return pose, len(good)

    def compute_transform(self, kp1, des1, kp2, des2):
        transform = None

        if des1 is not None and des2 is not None:
            matches = self.matcher.knnMatch(des1, des2, k=2)

            good = []
            for match in matches:
                if len(match) > 1 and match[0].distance < MATCH_RATIO * match[1].distance:
                    good.append(match[0])

            src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

            # estimateRigidTransform needs at least three pairs
            if src_pts is not None and dst_pts is not None and len(src_pts) > 3 and len(dst_pts) > 3:
                transform = cv2.estimateRigidTransform(src_pts, dst_pts, False)

        return transform


def adjustAngle(angle):
    """""
    keeps angle within -pi to pi
    """""
    while angle > math.pi:
        angle -= 2 * math.pi
    while angle <= -math.pi:
        angle += 2 * math.pi

    return angle

def create_map(file_name):
    """
    create a feature map, extract features from each bigger cell.
    :param file_name: the image of map
    :return: a list of center of bigger cells (kp and des), each bigger cell is a 3 by 3 grid (9 cells).
    """

    # read image and extract features
    image = cv2.imread(file_name)
    # the edgeThreshold and patchSize can be tuned if the gap between cell is too large
    detector = cv2.ORB(nfeatures=MAP_FEATURES, scoreType=cv2.ORB_FAST_SCORE)
    maxTotalKeypoints = 500 * ORB_GRID_SIZE_X * ORB_GRID_SIZE_Y
    detector_grid = cv2.GridAdaptedFeatureDetector(detector, maxTotalKeypoints=maxTotalKeypoints,
                                                   gridCols=ORB_GRID_SIZE_X, gridRows=ORB_GRID_SIZE_Y)
    kp = detector_grid.detect(image, None)
    kp, des = detector.compute(image, kp)

    # rearrange kp and des into grid
    grid_kp = [[[] for y in range(MAP_GRID_SIZE_Y)] for x in range(MAP_GRID_SIZE_X)]
    grid_des = [[[] for y in range(MAP_GRID_SIZE_Y)] for x in range(MAP_GRID_SIZE_X)]
    for i in range(len(kp)):
        x = int(kp[i].pt[0] / CELL_X)
        y = MAP_GRID_SIZE_Y - 1 - int(kp[i].pt[1] / CELL_Y)
        grid_kp[x][y].append(kp[i])
        grid_des[x][y].append(des[i])

    # group every 3 by 3 grid, so we can access each center of grid and its 8 neighbors easily
    map_grid_kp = [[[] for y in range(MAP_GRID_SIZE_Y)] for x in range(MAP_GRID_SIZE_X)]
    map_grid_des = [[[] for y in range(MAP_GRID_SIZE_Y)] for x in range(MAP_GRID_SIZE_X)]
    for i in range(MAP_GRID_SIZE_X):
        for j in range(MAP_GRID_SIZE_Y):
            for k in range(-1, 2):
                for l in range(-1, 2):
                    x = i + k
                    y = j + l
                    if 0 <= x < MAP_GRID_SIZE_X and 0 <= y < MAP_GRID_SIZE_Y:
                        map_grid_kp[i][j].extend(grid_kp[x][y])
                        map_grid_des[i][j].extend(grid_des[x][y])
            map_grid_des[i][j] = np.array(map_grid_des[i][j])

    return map_grid_kp, map_grid_des


def norm_pdf(x, mu, sigma):
    u = (x - mu) / float(abs(sigma))
    y = (1 / (np.sqrt(2 * np.pi) * abs(sigma))) * np.exp(-u * u / 2.)
    return y
