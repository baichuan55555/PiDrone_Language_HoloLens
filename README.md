# Accepted at the 2019 International Conference on Robotics and Automation (ICRA).
```
@inproceedings{huang19mrdrone,
  title={{Flight, Camera, Action! Using Natural Language and Mixed Reality to Control a Drone}},
  author={Huang, Baichuan and Bayazit, Deniz and Ullman, Daniel and Gopalan Nakul and Tellex, Stefanie},
  booktitle={{IEEE International Conference on Robotics and Automation (ICRA)}},
  year={2019}
}
```
# Run it requires three parts.
## HoloLens
Build in Unity
## PiDrone
Run pi.screenrc
## Base station
Run ba.screenrc

# PiDrone Pkg
https://github.com/h2r/pidrone_pkg
The web interface is in web/index_local.html

# Language model
See mdp folder

# Docker image
https://hub.docker.com/r/baichuan55555/hololens_drone/
Run docker_run.bash to create container.
Run speech.sh in host to transform voice to text.
