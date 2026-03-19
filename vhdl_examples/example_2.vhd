library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity bus_memory_controller is
    port (
        -- Clock and reset
        clk             : in    std_logic;
        reset_n         : in    std_logic;

        -- Host interface
        host_addr       : in    std_logic_vector(23 downto 0);
        host_data       : inout std_logic_vector(31 downto 0);
        host_wr_n       : in    std_logic;
        host_rd_n       : in    std_logic;
        host_cs_n       : in    std_logic;
        host_ack        : out   std_logic;
        host_irq        : buffer std_logic;

        -- Memory interface
        mem_addr        : out   std_logic_vector(23 downto 0);
        mem_data        : inout std_logic_vector(15 downto 0);
        mem_we_n        : out   std_logic;
        mem_oe_n        : out   std_logic;
        mem_ce_n        : out   std_logic;
        mem_ub_n        : out   std_logic;
        mem_lb_n        : out   std_logic;

        -- Status and control
        busy            : buffer std_logic;
        error_flag      : out   std_logic;
        debug_bus       : inout std_logic_vector(7 downto 0);
        config_in       : in    std_logic_vector(3 downto 0)
    );
end entity bus_memory_controller;

architecture rtl of bus_memory_controller is
begin
    -- Stub architecture
    host_ack   <= '0';
    host_irq   <= '0';
    mem_addr   <= (others => '0');
    mem_we_n   <= '1';
    mem_oe_n   <= '1';
    mem_ce_n   <= '1';
    mem_ub_n   <= '1';
    mem_lb_n   <= '1';
    busy       <= '0';
    error_flag <= '0';
    host_data  <= (others => 'Z');
    mem_data   <= (others => 'Z');
    debug_bus  <= (others => 'Z');
end architecture rtl;