e
# MUX16 channel select
set_gpio_cfg 0x3FFi 1
set_gpio 0i
var c
var p
set_pgstat_mode 2
set_max_bandwidth 40
set_range ba 1m
set_autoranging ba 59n 1m
set_e -700m
cell_on
# CV measurement loop
meas_loop_cv p c -700m -700m 0 2m 100m
	pck_start
	pck_add p
	pck_add c
	pck_end
endloop
on_finished:
cell_off