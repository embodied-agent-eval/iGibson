#!/usr/bin/python
import argparse
import os
import rospy
from std_msgs.msg import Float32, Int64, Header
from geometry_msgs.msg import Twist
from sensor_msgs import point_cloud2 as pc2
from sensor_msgs.msg import Image, CameraInfo, PointCloud2
from nav_msgs.msg import Odometry
import rospkg
import numpy as np
from cv_bridge import CvBridge
import tf
from gibson2.envs.locomotor_env import *
import matplotlib.pyplot as plt


class SimNode:
    def __init__(self):
        rospy.init_node('gibson2_sim')
        rospack = rospkg.RosPack()
        path = rospack.get_path('gibson2-ros')
        config_filename = os.path.join(path, 'turtlebot_rgbd.yaml')

        self.cmdx = 0.0
        self.cmdy = 0.0
        self.image_pub = rospy.Publisher("/gibson_ros/camera/rgb/image",Image, queue_size=10)
        self.depth_pub = rospy.Publisher("/gibson_ros/camera/depth/image",Image, queue_size=10)
        self.lidar_pub = rospy.Publisher("/gibson_ros/lidar/points", PointCloud2, queue_size=10)

        self.depth_raw_pub = rospy.Publisher("/gibson_ros/camera/depth/image_raw",Image, queue_size=10)
        self.odom_pub = rospy.Publisher("/odom",Odometry, queue_size=10)
        self.gt_odom_pub = rospy.Publisher("/ground_truth_odom", Odometry, queue_size=10)

        self.camera_info_pub = rospy.Publisher("/gibson_ros/camera/depth/camera_info", CameraInfo, queue_size=10)
        self.bridge = CvBridge()
        self.br = tf.TransformBroadcaster()

        self.env = NavigateEnv(config_file=config_filename, mode='headless', action_timestep=1/30.0) # assume a 30Hz simulation
        print(self.env.config)

        obs = self.env.reset()
        rospy.Subscriber("/mobile_base/commands/velocity", Twist, self.cmd_callback)

        # self.add_objects(self.env)

    @staticmethod
    def add_objects(env):
        from gibson2.core.physics.interactive_objects import ShapeNetObject
        # obj_path = '/cvgl/group/ShapeNetCore.v2/03001627/1b05971a4373c7d2463600025db2266/models/model_normalized.obj'
        obj_path = '/cvgl/group/ShapeNetCore.v2/03001627/60b3d70238246b3e408442c6701ebe92/models/model_normalized.obj'
        cur_obj = ShapeNetObject(obj_path, scale=1.0, position=[0, -2.0, 0.5], orientation=[0, 0, np.pi])
        env.simulator.import_object(cur_obj)

    def run(self):
        while not rospy.is_shutdown():
            obs, _, _, _ = self.env.step([self.cmdx, self.cmdy])
            rgb = (obs["rgb"]*255).astype(np.uint8)
            depth = obs["depth"].astype(np.float32)
            depth[depth > 10] = np.nan
            depth[depth < 0.45] = np.nan
            image_message = self.bridge.cv2_to_imgmsg(rgb, encoding="rgb8")
            depth_raw_image = (obs["depth"] * 1000).astype(np.uint16)
            depth_raw_message = self.bridge.cv2_to_imgmsg(depth_raw_image, encoding="passthrough")
            depth_message = self.bridge.cv2_to_imgmsg(depth, encoding="passthrough")

            now = rospy.Time.now()

            image_message.header.stamp = now
            depth_message.header.stamp = now
            depth_raw_message.header.stamp = now
            image_message.header.frame_id = "camera_depth_optical_frame"
            depth_message.header.frame_id = "camera_depth_optical_frame"
            depth_raw_message.header.frame_id = "camera_depth_optical_frame"

            self.image_pub.publish(image_message)
            self.depth_pub.publish(depth_message)
            self.depth_raw_pub.publish(depth_raw_message)
            msg = CameraInfo(height=256, width=256, distortion_model="plumb_bob", D=[0.0, 0.0, 0.0, 0.0, 0.0],
                             K=[128, 0.0, 128.5, 0.0, 128, 128.5, 0.0, 0.0, 1.0],
                             R=[1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                             P=[128, 0.0, 128.5, -0.0, 0.0, 128, 128.5, 0.0, 0.0, 0.0, 1.0, 0.0])
            msg.header.stamp = now
            msg.header.frame_id = "camera_depth_optical_frame"
            self.camera_info_pub.publish(msg)

            lidar_points = obs['lidar']
            lidar_header = Header()
            lidar_header.stamp = now
            lidar_header.frame_id = 'scan_link'
            lidar_message = pc2.create_cloud_xyz32(lidar_header, lidar_points.tolist())
            self.lidar_pub.publish(lidar_message)

            # odometry

            odom = [np.array(self.env.robots[0].body_xyz) - np.array(self.env.config["initial_pos"]),
                    np.array(self.env.robots[0].body_rpy)]

            self.br.sendTransform((odom[0][0], odom[0][1], 0),
                             tf.transformations.quaternion_from_euler(0, 0, odom[-1][-1]),
                             rospy.Time.now(),
                             'base_footprint',
                             "odom")
            odom_msg = Odometry()
            odom_msg.header.stamp = rospy.Time.now()
            odom_msg.header.frame_id = 'odom'
            odom_msg.child_frame_id = 'base_footprint'
    
            odom_msg.pose.pose.position.x = odom[0][0]
            odom_msg.pose.pose.position.y = odom[0][1]
            odom_msg.pose.pose.orientation.x, odom_msg.pose.pose.orientation.y, odom_msg.pose.pose.orientation.z, \
            odom_msg.pose.pose.orientation.w = tf.transformations.quaternion_from_euler(0, 0, odom[-1][-1])
    
            odom_msg.twist.twist.linear.x = (self.cmdx + self.cmdy) * 5
            odom_msg.twist.twist.angular.z = (self.cmdy - self.cmdx) * 5 * 8.695652173913043
            self.odom_pub.publish(odom_msg)

            # Ground truth pose
            gt_odom_msg = Odometry()
            gt_odom_msg.header.stamp = rospy.Time.now()
            gt_odom_msg.header.frame_id = 'ground_truth_odom'
            gt_odom_msg.child_frame_id = 'base_footprint'

            gt_odom_msg.pose.pose.position.x = self.env.robots[0].body_xyz[0]
            gt_odom_msg.pose.pose.position.y = self.env.robots[0].body_xyz[1]
            gt_odom_msg.pose.pose.position.z = self.env.robots[0].body_xyz[2]
            gt_odom_msg.pose.pose.orientation.x, gt_odom_msg.pose.pose.orientation.y, gt_odom_msg.pose.pose.orientation.z, \
                gt_odom_msg.pose.pose.orientation.w = tf.transformations.quaternion_from_euler(
                    self.env.robots[0].body_rpy[0],
                    self.env.robots[0].body_rpy[1],
                    self.env.robots[0].body_rpy[2])

            gt_odom_msg.twist.twist.linear.x = (self.cmdx + self.cmdy) * 5
            gt_odom_msg.twist.twist.angular.z = (self.cmdy - self.cmdx) * 5 * 8.695652173913043
            self.gt_odom_pub.publish(gt_odom_msg)

        rospy.spin()

    def cmd_callback(self,data):
        self.cmdx = data.linear.x/10.0 - data.angular.z / (10*8.695652173913043)
        self.cmdy = data.linear.x/10.0 + data.angular.z / (10*8.695652173913043)


if __name__ == '__main__':
    node = SimNode()
    node.run()